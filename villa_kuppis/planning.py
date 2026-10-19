"""Fixed-threshold planning (mode 1 of docs/spot-price.md).

Pure functions: periods in, per-room per-period targets out. No I/O, no
device actions, no off rules, no optimizer (those come in F4.1).

Settings schema (JSON)::

    {
      "site": {
        "base_temperature_c": 19.0,
        "rules": [
          {"kind": "heat", "threshold_c_kwh": 8.0, "target_c": 21.0},
          {"kind": "heat", "threshold_c_kwh": 5.0, "target_c": 24.0}
        ]
      },
      "rooms": {
        "sauna": {},                              # inherits everything
        "guest_room": {"base_temperature_c": 16.0},  # per-field override
        "office": {"rules": []},                  # explicit [] disables
                                                       inherited rules
        "pump_room": {"rules": [                  # cooling lives in its
          {"kind": "cool", "threshold_c_kwh": 5.0,   # own room; a room
           "target_c": 17.0}]}                     # never mixes both kinds
      }
    }

Semantics per room and period (price ``p``, base ``b``):

* A rule matches when ``p < threshold`` (strict; equal never matches).
* Heating room: if any heat rule matches, the target is the highest
  matching target, never below the base: ``max(b, max(matching targets))``.
* Cooling room: if any cool rule matches, the target is the lowest
  matching target, never above the base: ``min(b, min(matching
  targets))``.
* No matching rule (or no rules at all): the base.

A room's effective rules (the site rules, or the room's own list) must be
all heat or all cool; mixing both kinds in one room raises ``ValueError``.
Heating and cooling are different units in this house (docs/spot-price.md
keeps them apart), so "both kinds match" is a configuration error, not a
tie to break. Distinct rooms may use different kinds via overrides.
Because selection is always an extremum (max target / min target, with
threshold as the deterministic tie-break), the result is independent of
the order of the rules. The reason is ``"threshold"`` when a rule matched
(even if the clamp lands on the base) and ``"base"`` otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Sequence

#: A normalized rule: (kind, threshold_c_kwh, target_c), all Decimals.
Rule = tuple[str, Decimal, Decimal]


@dataclass(frozen=True)
class Period:
    """One 15-minute price period."""

    ts_utc: int
    price_c_kwh: Decimal


@dataclass(frozen=True)
class RoomPlan:
    """The planned target for one room in one period."""

    ts_utc: int
    price_c_kwh: Decimal
    temperature_c: Decimal
    reason: str                      # "base" or "threshold"
    matched_threshold_c_kwh: Decimal | None


def _to_decimal(value: object, what: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{what} must be a number, got {type(value).__name__}")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"{what} is not a number: {value!r}") from exc
    if not number.is_finite():
        raise ValueError(f"{what} must be finite: {value!r}")
    return number


def _validate_rule(rule: object, what: str) -> Rule:
    if not isinstance(rule, dict):
        raise ValueError(f"{what} must be an object, got {type(rule).__name__}")
    kind = rule.get("kind")
    if kind not in ("heat", "cool"):
        raise ValueError(f"{what}.kind must be 'heat' or 'cool', got {kind!r}")
    threshold = _to_decimal(rule.get("threshold_c_kwh"), f"{what}.threshold_c_kwh")
    target = _to_decimal(rule.get("target_c"), f"{what}.target_c")
    return kind, threshold, target


def _validate_rules(rules: object, what: str) -> list[Rule]:
    if not isinstance(rules, list):
        raise ValueError(f"{what} must be a list, got {type(rules).__name__}")
    return [_validate_rule(r, f"{what}[{i}]") for i, r in enumerate(rules)]


def _check_not_mixed(rules: Sequence[Rule], what: str) -> None:
    """Reject a rule list that contains both 'heat' and 'cool'."""
    if {"heat", "cool"} <= {kind for kind, _, _ in rules}:
        raise ValueError(
            f"{what} mixes 'heat' and 'cool' rules; each room may use one "
            "kind only (other rooms get their own overrides)"
        )


def normalize_settings(settings: object) -> tuple[Decimal, list[Rule],
                                                 dict[str, tuple[Decimal | None, list[Rule] | None]]]:
    """Validate a settings dict.

    Returns ``(site_base, site_rules, rooms)`` where each room maps to
    ``(base override or None, rules override or None)``; ``None`` means
    "inherit from the site". An explicit empty rule list disables the
    inherited rules.
    """
    if not isinstance(settings, dict):
        raise ValueError("settings must be a JSON object")
    site = settings.get("site")
    if not isinstance(site, dict):
        raise ValueError("settings.site is required and must be an object")
    if "base_temperature_c" not in site:
        raise ValueError("settings.site.base_temperature_c is required")
    site_base = _to_decimal(site["base_temperature_c"],
                            "settings.site.base_temperature_c")
    site_rules = _validate_rules(site.get("rules", []), "settings.site.rules")
    _check_not_mixed(site_rules, "settings.site.rules")

    rooms_in = settings.get("rooms", {})
    if not isinstance(rooms_in, dict):
        raise ValueError("settings.rooms must be an object")
    rooms: dict[str, tuple[Decimal | None, list[Rule] | None]] = {}
    for name, room in rooms_in.items():
        if not isinstance(room, dict):
            raise ValueError(f"settings.rooms.{name} must be an object")
        base = (_to_decimal(room["base_temperature_c"],
                            f"settings.rooms.{name}.base_temperature_c")
                if "base_temperature_c" in room else None)
        rules = (_validate_rules(room["rules"], f"settings.rooms.{name}.rules")
                 if "rules" in room else None)
        _check_not_mixed(rules if rules is not None else site_rules,
                         f"room {name} effective rules")
        rooms[name] = (base, rules)
    return site_base, site_rules, rooms


def plan_day(periods: Sequence[Period], settings: object) -> dict[str, list[RoomPlan]]:
    """Compute the plan for every room and period. Pure function."""
    site_base, site_rules, rooms = normalize_settings(settings)
    result: dict[str, list[RoomPlan]] = {}
    for name, (base_override, rules_override) in rooms.items():
        base = base_override if base_override is not None else site_base
        rules = rules_override if rules_override is not None else site_rules
        heat = [r for r in rules if r[0] == "heat"]
        cool = [r for r in rules if r[0] == "cool"]
        plans: list[RoomPlan] = []
        for period in periods:
            p = period.price_c_kwh
            if heat:
                # Heating room: highest matching target; the threshold
                # only breaks target ties.
                matching = [r for r in heat if p < r[1]]
                if matching:
                    target, threshold = max((r[2], r[1]) for r in matching)
                    plans.append(RoomPlan(period.ts_utc, p, max(base, target),
                                          "threshold", threshold))
                    continue
            elif cool:
                # Cooling room: lowest matching target; the threshold
                # only breaks target ties.
                matching = [r for r in cool if p < r[1]]
                if matching:
                    target, threshold = min((r[2], r[1]) for r in matching)
                    plans.append(RoomPlan(period.ts_utc, p, min(base, target),
                                          "threshold", threshold))
                    continue
            plans.append(RoomPlan(period.ts_utc, p, base, "base", None))
        result[name] = plans
    return result
