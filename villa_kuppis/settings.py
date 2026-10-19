"""In-app settings (card F3.5).

Every control setting has a default (docs/spot-price.md) and can be
overridden per site or per room; a room override wins over the site
override, which wins over the default. Overrides live in the
``setting`` table of ``pumps.db`` (so the daily backup covers them) and
are read fresh by every plan, so a change takes effect in the next plan
without a restart.

Scopes are ``site:<site>`` and ``room:<site>/<room>``. The pure helpers
at the bottom (``resolve``, ``plan_settings``) turn the stored overrides
into the settings object that ``planning.plan_day`` consumes, so the plan
code itself stays a pure function.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

SITES = ("home", "cottage")
SITE_LABELS = {"home": "Koti", "cottage": "Mökki"}

#: Per-site default for the one setting that differs between the sites.
SITE_DEFAULT_KIND = {"home": "cool", "cottage": "heat"}

MAX_ACTOR_LENGTH = 200
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_SCOPE_RE = re.compile(r"^(site:(home|cottage)|room:(home|cottage)/[^/\x00-\x1f\x7f]{1,100})$")


@dataclass(frozen=True)
class Definition:
    """One setting: key, Finnish label, group, type, default and bounds."""

    key: str
    label: str
    group: str
    type: str                      # number | bool | time | choice
    default: object
    unit: str = ""
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[tuple[str, str], ...] = ()

    def public(self) -> dict:
        return {"key": self.key, "label": self.label, "group": self.group,
                "type": self.type, "default": self.default,
                "unit": self.unit, "min": self.minimum, "max": self.maximum,
                "choices": [{"value": v, "label": l} for v, l in self.choices]}


DEFINITIONS: tuple[Definition, ...] = (
    Definition("kind", "Ohjaustapa", "Yleiset", "choice", "cool",
               choices=(("cool", "Jäähdytys"), ("heat", "Lämmitys"))),
    Definition("low_price_c_kwh", "Halpa hinta", "Pörssihinta", "number", 5.0,
               "c/kWh", -50, 500),
    Definition("high_price_c_kwh", "Kallis hinta", "Pörssihinta", "number",
               15.0, "c/kWh", -50, 500),
    Definition("cool_max_c", "Enimmäislämpötila", "Jäähdytys", "number", 25.0,
               "°C", 15, 35),
    Definition("precool_c", "Esijäähdytyslämpötila", "Jäähdytys", "number",
               21.0, "°C", 15, 30),
    Definition("cool_off_when_expensive", "Sammuta kalliilla hinnalla",
               "Jäähdytys", "bool", True),
    Definition("min_off_minutes", "Vähimmäistaukoaika", "Jäähdytys", "number",
               30, "min", 0, 240),
    Definition("cool_optimizer", "Optimoitu jäähdytys", "Jäähdytys", "bool",
               True),
    Definition("away_max_c", "Enimmäislämpötila poissa kotoa", "Poissa kotoa",
               "number", 28.0, "°C", 15, 40),
    Definition("night_enabled", "Yötila käytössä", "Yötila", "bool", False),
    Definition("night_start", "Yötilan alku", "Yötila", "time", "22:00"),
    Definition("night_end", "Yötilan loppu", "Yötila", "time", "07:00"),
    Definition("heat_base_c", "Perustaso (lämmitys)", "Lämmitys", "number",
               5.0, "°C", 0, 30),
    Definition("heat_rule1_price_c_kwh", "Hintaraja 1: hinta alle", "Lämmitys",
               "number", 8.0, "c/kWh", -50, 500),
    Definition("heat_rule1_c", "Hintaraja 1: tavoitelämpötila", "Lämmitys",
               "number", 8.0, "°C", 0, 30),
    Definition("heat_rule2_price_c_kwh", "Hintaraja 2: hinta alle", "Lämmitys",
               "number", 5.0, "c/kWh", -50, 500),
    Definition("heat_rule2_c", "Hintaraja 2: tavoitelämpötila", "Lämmitys",
               "number", 12.0, "°C", 0, 30),
    Definition("smart_enabled", "Älykäs ylläpitolämmitys", "Lämmitys", "bool",
               False),
    Definition("smart_min_c", "Vähimmäislämpötila", "Lämmitys", "number", 5.0,
               "°C", 0, 30),
    Definition("smart_max_c", "Suurin varaava lämpötila", "Lämmitys",
               "number", 12.0, "°C", 0, 30),
)
BY_KEY = {d.key: d for d in DEFINITIONS}


def validate_value(key: str, value: object) -> object:
    """Return the normalized value for ``key`` or raise ValueError (Finnish)."""
    definition = BY_KEY.get(key)
    if definition is None:
        raise ValueError("tuntematon asetus")
    if definition.type == "bool":
        if not isinstance(value, bool):
            raise ValueError("arvon on oltava päällä tai pois")
        return value
    if definition.type == "choice":
        if value not in dict(definition.choices):
            raise ValueError("virheellinen valinta")
        return value
    if definition.type == "time":
        if not isinstance(value, str) or not _TIME_RE.match(value):
            raise ValueError("kellonajan muoto on tt:mm")
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("arvon on oltava luku")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError("arvon on oltava äärellinen luku")
    if definition.minimum is not None and number < definition.minimum:
        raise ValueError(f"arvon on oltava vähintään {definition.minimum:g}")
    if definition.maximum is not None and number > definition.maximum:
        raise ValueError(f"arvon on oltava enintään {definition.maximum:g}")
    if isinstance(definition.default, int) and not isinstance(definition.default, bool):
        if number != int(number):
            raise ValueError("arvon on oltava kokonaisluku")
        return int(number)
    return number


def _check_consistent(values: Mapping[str, object]) -> None:
    """Cross-field rules on a fully resolved value set."""
    if values["precool_c"] > values["cool_max_c"]:
        raise ValueError("esijäähdytyslämpötila ei voi ylittää enimmäislämpötilaa")
    if values["away_max_c"] < values["cool_max_c"]:
        raise ValueError("poissaoloraja ei voi olla enimmäislämpötilaa pienempi")
    if values["low_price_c_kwh"] > values["high_price_c_kwh"]:
        raise ValueError("halpa hinta ei voi ylittää kallista hintaa")
    if values["smart_min_c"] > values["smart_max_c"]:
        raise ValueError("vähimmäislämpötila ei voi ylittää suurinta varaavaa lämpötilaa")


def parse_scope(scope: str) -> tuple[str, str | None]:
    """Return ``(site, room or None)``; ValueError for a malformed scope."""
    if not isinstance(scope, str) or not _SCOPE_RE.match(scope):
        raise ValueError("virheellinen kohde")
    if scope.startswith("site:"):
        return scope[5:], None
    site, room = scope[5:].split("/", 1)
    return site, room


def resolve(overrides: Mapping[str, Mapping[str, object]], site: str,
            room: str | None = None) -> dict[str, dict]:
    """Effective values with their source: ``{key: {value, source}}``."""
    out: dict[str, dict] = {}
    site_over = overrides.get(f"site:{site}", {})
    room_over = overrides.get(f"room:{site}/{room}", {}) if room else {}
    for d in DEFINITIONS:
        default = SITE_DEFAULT_KIND[site] if d.key == "kind" else d.default
        if d.key in room_over:
            out[d.key] = {"value": room_over[d.key], "source": "room"}
        elif d.key in site_over:
            out[d.key] = {"value": site_over[d.key], "source": "site"}
        else:
            out[d.key] = {"value": default, "source": "default"}
    return out


def _values(resolved: Mapping[str, Mapping]) -> dict[str, object]:
    return {k: v["value"] for k, v in resolved.items()}


def plan_settings(overrides: Mapping[str, Mapping[str, object]], site: str,
                  rooms: Sequence[str]) -> dict:
    """Build the ``planning.plan_day`` settings object for one site.

    Home rooms plan cooling (base = maximum temperature, one precool rule
    below the low price); cottage rooms plan heating (base level plus the
    two price rules). Every room is written out explicitly from its
    resolved values, so a room override of any setting reaches the plan.
    """
    def entry(values: Mapping[str, object]) -> dict:
        if values["kind"] == "cool":
            return {"base_temperature_c": values["cool_max_c"],
                    "rules": [{"kind": "cool",
                               "threshold_c_kwh": values["low_price_c_kwh"],
                               "target_c": values["precool_c"]}]}
        return {"base_temperature_c": values["heat_base_c"],
                "rules": [
                    {"kind": "heat",
                     "threshold_c_kwh": values["heat_rule1_price_c_kwh"],
                     "target_c": values["heat_rule1_c"]},
                    {"kind": "heat",
                     "threshold_c_kwh": values["heat_rule2_price_c_kwh"],
                     "target_c": values["heat_rule2_c"]}]}

    site_entry = entry(_values(resolve(overrides, site)))
    return {"site": site_entry,
            "rooms": {room: entry(_values(resolve(overrides, site, room)))
                      for room in rooms}}


class SettingsStore:
    """SQLite-backed overrides. Short-lived connections, safe across threads."""

    def __init__(self, path: str | Path, *, now=None):
        self._path = Path(path)
        self._now = now or (lambda: datetime.now(timezone.utc))
        conn = self._connect()
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS setting ("
                "scope TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, "
                "updated_at TEXT NOT NULL, updated_by TEXT NOT NULL, "
                "PRIMARY KEY (scope, key))")
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), timeout=5.0,
                               isolation_level=None)
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def overrides(self) -> dict[str, dict[str, object]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT scope, key, value FROM setting").fetchall()
        finally:
            conn.close()
        out: dict[str, dict[str, object]] = {}
        for scope, key, raw in rows:
            if key in BY_KEY:
                out.setdefault(scope, {})[key] = json.loads(raw)
        return out

    def audit(self) -> dict[str, dict[str, dict]]:
        """``{scope: {key: {updated_at, updated_by}}}`` for the UI."""
        conn = self._connect()
        try:
            rows = conn.execute("SELECT scope, key, updated_at, updated_by "
                                "FROM setting").fetchall()
        finally:
            conn.close()
        out: dict[str, dict[str, dict]] = {}
        for scope, key, at, by in rows:
            out.setdefault(scope, {})[key] = {"updated_at": at,
                                              "updated_by": by}
        return out

    def set(self, scope: str, key: str, value: object, actor: str) -> object:
        """Validate and store an override; ValueError on a bad change."""
        site, room = parse_scope(scope)
        value = validate_value(key, value)
        actor = str(actor)[:MAX_ACTOR_LENGTH]
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                current = self._read(conn)
                current.setdefault(scope, {})[key] = value
                self._check_scope(current, site, room)
                conn.execute(
                    "INSERT INTO setting (scope, key, value, updated_at, "
                    "updated_by) VALUES (?, ?, ?, ?, ?) ON CONFLICT(scope, key) "
                    "DO UPDATE SET value = excluded.value, "
                    "updated_at = excluded.updated_at, "
                    "updated_by = excluded.updated_by",
                    (scope, key, json.dumps(value), self._now().isoformat(),
                     actor))
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()
        return value

    def reset(self, scope: str, key: str) -> None:
        """Remove an override so the inherited value applies again."""
        site, room = parse_scope(scope)
        if key not in BY_KEY:
            raise ValueError("tuntematon asetus")
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("DELETE FROM setting WHERE scope = ? AND key = ?",
                             (scope, key))
                # Removing a site override can expose a room/default pair
                # that no longer fits together; refuse such a reset.
                self._check_site(self._read(conn), site)
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()

    @staticmethod
    def _read(conn) -> dict[str, dict[str, object]]:
        out: dict[str, dict[str, object]] = {}
        for scope, key, raw in conn.execute(
                "SELECT scope, key, value FROM setting"):
            if key in BY_KEY:
                out.setdefault(scope, {})[key] = json.loads(raw)
        return out

    @staticmethod
    def _check_scope(overrides, site: str, room: str | None) -> None:
        if room is not None:
            _check_consistent(_values(resolve(overrides, site, room)))
            return
        SettingsStore._check_site(overrides, site)

    @staticmethod
    def _check_site(overrides, site: str) -> None:
        _check_consistent(_values(resolve(overrides, site)))
        prefix = f"room:{site}/"
        for scope in overrides:
            if scope.startswith(prefix):
                _check_consistent(_values(resolve(overrides, site,
                                                  scope[len(prefix):])))
