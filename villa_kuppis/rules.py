"""Villa Kuppis rules for Home Assistant devices (devices.py).

Rules are data made in the web page, so a new device joins a rule without a
release. Four kinds:

- ``between``: one or more devices are on from a start moment to an end
  moment (a time of day, or sunrise or sunset with an offset), off
  otherwise. Festive lights from sunset to 23:00, or the living room and
  kitchen plugs from 7:00 to 9:00 on weekdays.
- ``cheapest``: devices are on in the cheapest 15-minute periods of a daily
  window, for the given number of hours, off otherwise.
- ``level``: devices follow a sensor with a dead band: on below (or above)
  one value, off again past another. A plug heater, fan or dehumidifier.
- ``event``: when any of one to five things happens (a time or sun moment,
  a sensor goes above or below a value, a device or binary sensor turns on
  or off, the spot price goes under or over a value, someone comes home or
  the last one leaves), if the conditions allow, send up to ten device
  actions; optionally switch the devices off again after some minutes.

Every kind takes optional conditions, all of which must hold: weekdays, a
daily time window, a price limit and up to five "only when" checks (a
sensor above or below a value, a device on or off, someone home or nobody
home). For ``between``, ``cheapest`` and ``level`` a failing condition
means off.

``between``, ``cheapest`` and ``level`` act only when the wanted state
changes, so a person's own change holds until the next change point.
Rules do not follow the site's Automaatio switch, which is only the home
cooling and cottage heating automation (Ville, 2026-11-08); a rule runs
while it is enabled. ``automation_on`` stays as a hook for tests. A pending
"off after" of an event rule runs even after the rule is disabled. Sun times come from Home Assistant's ``sun.sun`` entity; presence
from its ``person`` entities.
"""

from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

HELSINKI = ZoneInfo("Europe/Helsinki")
PERIOD = 900
GRACE = timedelta(minutes=30)     # a missed moment still runs this late (restart)
RETRY = timedelta(minutes=5)      # a failed on/off change is tried again after this
MAX_ACTIONS = 10
MAX_DEVICES = 10
MAX_TRIGGERS = 5
MAX_CONDITIONS = 5
MAX_NAME = 60
MAX_OFF_AFTER = 24 * 60           # minutes
KINDS = ("between", "cheapest", "level", "event")
STATE_KINDS = ("between", "cheapest", "level")
TRIGGERS = ("time", "sun", "above", "below", "turns_on", "turns_off",
            "price_below", "price_above", "arrive", "leave")
CONDITIONS = ("above", "below", "is_on", "is_off", "home", "away")
ACTIONS = ("power", "brightness", "color_temp", "position", "cover", "speed", "setpoint", "run")
# Actions that leave a device on, so "off after" switches it off again.
ON_ACTIONS = ("brightness", "color_temp", "speed")
_HHMM = re.compile(r"([01]\d|2[0-3]):([0-5]\d)\Z")
_WORST = ("confirmed", "pending", "unknown", "failed")


class RuleError(ValueError):
    """An invalid rule; the message is Finnish."""


# --- validation ------------------------------------------------------------------------

def _hhmm(value, what: str) -> str:
    if not isinstance(value, str) or not _HHMM.match(value):
        raise RuleError(f"{what}: kellonaika muodossa HH:MM")
    return value


def _num(value, what: str, lo: float, hi: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise RuleError(f"{what}: luku")
    if not lo <= value <= hi:
        raise RuleError(f"{what}: {lo:g}–{hi:g}")
    return float(value)


def _key(value, what: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 200:
        raise RuleError(f"{what}: valitse laite")
    return value


def _keys(rule: dict) -> list[str]:
    """The rule's devices: ``keys``, or the single ``key`` of older rules."""
    raw = rule.get("keys")
    if raw is None and rule.get("key") is not None:
        raw = [rule["key"]]
    if not isinstance(raw, list) or not raw:
        raise RuleError("Valitse vähintään yksi laite.")
    out = []
    for k in raw:
        k = _key(k, "Laite")
        if k not in out:
            out.append(k)
    if len(out) > MAX_DEVICES:
        raise RuleError(f"Enintään {MAX_DEVICES} laitetta.")
    return out


def _moment(value, what: str) -> dict:
    if not isinstance(value, dict):
        raise RuleError(f"{what}: valitse hetki")
    if value.get("type") == "time":
        return {"type": "time", "at": _hhmm(value.get("at"), what)}
    if value.get("type") == "sun":
        if value.get("event") not in ("sunrise", "sunset"):
            raise RuleError(f"{what}: auringonnousu tai -lasku")
        offset = int(_num(value.get("offset", 0), f"{what} siirto (min)", -240, 240))
        return {"type": "sun", "event": value["event"], "offset": offset}
    raise RuleError(f"{what}: kellonaika tai aurinko")


def _trigger(trigger) -> dict:
    if not isinstance(trigger, dict) or trigger.get("type") not in TRIGGERS:
        raise RuleError("Valitse, mistä sääntö käynnistyy.")
    t = trigger["type"]
    if t in ("time", "sun"):
        return _moment(trigger, "Hetki")
    if t in ("above", "below"):
        return {"type": t, "key": _key(trigger.get("key"), "Anturi"),
                "value": _num(trigger.get("value"), "Raja", -1e6, 1e6)}
    if t in ("price_below", "price_above"):
        return {"type": t, "value": _num(trigger.get("value"), "Hintaraja (snt/kWh)", -100, 1000)}
    if t in ("turns_on", "turns_off"):
        return {"type": t, "key": _key(trigger.get("key"), "Laite")}
    return {"type": t}


def _condition(cond) -> dict:
    if not isinstance(cond, dict) or cond.get("type") not in CONDITIONS:
        raise RuleError("Tuntematon ehto.")
    t = cond["type"]
    if t in ("above", "below"):
        return {"type": t, "key": _key(cond.get("key"), "Ehdon anturi"),
                "value": _num(cond.get("value"), "Ehdon raja", -1e6, 1e6)}
    if t in ("is_on", "is_off"):
        return {"type": t, "key": _key(cond.get("key"), "Ehdon laite")}
    return {"type": t}


def _action(a) -> dict:
    if not isinstance(a, dict) or a.get("action") not in ACTIONS:
        raise RuleError("Toiminto puuttuu.")
    action, value = a["action"], a.get("value")
    if action == "power":
        if not isinstance(value, bool):
            raise RuleError("Päälle tai pois.")
    elif action == "brightness":
        value = _num(value, "Kirkkaus %", 1, 100)
    elif action == "color_temp":
        value = _num(value, "Värilämpötila (K)", 1000, 10000)
    elif action in ("position", "speed"):
        value = _num(value, "Arvo %", 0, 100)
    elif action == "setpoint":
        value = _num(value, "Tavoitelämpötila", 0, 40)
    elif action == "cover":
        if value not in ("open", "close", "stop"):
            raise RuleError("Avaa, sulje tai pysäytä.")
    else:
        value = True
    return {"key": _key(a.get("key"), "Toiminnon laite"), "action": action, "value": value}


def _conditions(rule: dict, out: dict) -> None:
    """The optional conditions every kind takes, copied into ``out``."""
    days = rule.get("days")
    if days is not None:
        if (not isinstance(days, list) or not days
                or any(isinstance(d, bool) or not isinstance(d, int) or not 0 <= d <= 6 for d in days)):
            raise RuleError("Valitse vähintään yksi viikonpäivä.")
        days = sorted(set(days))
        if len(days) < 7:
            out["days"] = days
    if rule.get("only"):
        only = rule["only"]
        if not isinstance(only, dict):
            raise RuleError("Aikaväli puuttuu.")
        out["only"] = {"start": _hhmm(only.get("start"), "Vain alkaen"),
                       "end": _hhmm(only.get("end"), "Vain asti")}
    if rule.get("max_price") is not None:
        out["max_price"] = _num(rule["max_price"], "Hintaraja (snt/kWh)", -100, 1000)
    when = rule.get("when")
    if when:
        if not isinstance(when, list) or len(when) > MAX_CONDITIONS:
            raise RuleError(f"Enintään {MAX_CONDITIONS} ehtoa.")
        out["when"] = [_condition(c) for c in when]


def validate(rule) -> dict:
    """A clean copy of a rule from the web page; raises RuleError."""
    if not isinstance(rule, dict):
        raise RuleError("Sääntö puuttuu.")
    kind = rule.get("kind")
    if kind not in KINDS:
        raise RuleError("Tuntematon säännön tyyppi.")
    name = rule.get("name") or ""
    if not isinstance(name, str):
        raise RuleError("Nimen täytyy olla tekstiä.")
    out = {"kind": kind, "name": " ".join(name.split())[:MAX_NAME]}
    if kind == "between":
        out.update(keys=_keys(rule),
                   start=_moment(rule.get("start"), "Alku"), end=_moment(rule.get("end"), "Loppu"))
    elif kind == "cheapest":
        out.update(keys=_keys(rule),
                   hours=_num(rule.get("hours"), "Tunnit", 0.25, 24),
                   start=_hhmm(rule.get("start"), "Ikkunan alku"),
                   end=_hhmm(rule.get("end"), "Ikkunan loppu"))
    elif kind == "level":
        mode = rule.get("mode")
        if mode not in ("below", "above"):
            raise RuleError("Valitse, kytketäänkö päälle alle vai yli rajan.")
        on_at = _num(rule.get("on_at"), "Päälle-raja", -1e6, 1e6)
        off_at = _num(rule.get("off_at"), "Pois-raja", -1e6, 1e6)
        if mode == "below" and off_at < on_at:
            raise RuleError("Pois-rajan täytyy olla vähintään päälle-raja.")
        if mode == "above" and off_at > on_at:
            raise RuleError("Pois-rajan täytyy olla enintään päälle-raja.")
        out.update(keys=_keys(rule), sensor=_key(rule.get("sensor"), "Anturi"),
                   mode=mode, on_at=on_at, off_at=off_at)
    else:
        triggers = rule.get("triggers")
        if triggers is None and rule.get("trigger") is not None:
            triggers = [rule["trigger"]]
        if not isinstance(triggers, list) or not 1 <= len(triggers) <= MAX_TRIGGERS:
            raise RuleError(f"Valitse 1–{MAX_TRIGGERS} käynnistäjää.")
        out["triggers"] = [_trigger(t) for t in triggers]
        actions = rule.get("actions")
        if not isinstance(actions, list) or not 1 <= len(actions) <= MAX_ACTIONS:
            raise RuleError(f"Lisää 1–{MAX_ACTIONS} toimintoa.")
        out["actions"] = [_action(a) for a in actions]
        if rule.get("off_after") is not None:
            out["off_after"] = int(_num(rule["off_after"], "Pois päältä (min)", 1, MAX_OFF_AFTER))
    _conditions(rule, out)
    return out


def upgrade(body: dict) -> dict:
    """A stored rule in today's shape (older rules had one ``key`` or ``trigger``)."""
    body = dict(body)
    if "key" in body and "keys" not in body:
        body["keys"] = [body.pop("key")]
    if "trigger" in body and "triggers" not in body:
        body["triggers"] = [body.pop("trigger")]
    return body


def rule_keys(body: dict) -> list[str]:
    """Every device key a rule uses (for the page's missing-device check)."""
    keys = list(body.get("keys", []))
    if body.get("sensor"):
        keys.append(body["sensor"])
    keys += [t["key"] for t in body.get("triggers", []) if t.get("key")]
    keys += [c["key"] for c in body.get("when", []) if c.get("key")]
    keys += [a["key"] for a in body.get("actions", [])]
    return list(dict.fromkeys(keys))


def off_keys(body: dict) -> list[str]:
    """The devices an event rule's "off after" switches off again."""
    return list(dict.fromkeys(
        a["key"] for a in body.get("actions", [])
        if (a["action"] == "power" and a["value"] is True) or a["action"] in ON_ACTIONS))


# --- time ----------------------------------------------------------------------------

def _parse(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        out = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return out if out.tzinfo else None


def moment_on(moment: dict, day: date, sun: dict | None) -> datetime | None:
    """The moment on a local day (UTC), or None without a sun time.

    Home Assistant gives the next sunrise and sunset; another day's is that
    time of day moved onto the day, a few minutes off at most.
    """
    if moment["type"] == "time":
        hh, mm = map(int, moment["at"].split(":"))
        local = datetime(day.year, day.month, day.day, hh, mm, tzinfo=HELSINKI)
        return local.astimezone(timezone.utc)
    attrs = (sun or {}).get("attributes") or {}
    nxt = _parse(attrs.get("next_setting" if moment["event"] == "sunset" else "next_rising"))
    if nxt is None:
        return None
    local = nxt.astimezone(HELSINKI)
    moved = datetime(day.year, day.month, day.day, local.hour, local.minute, local.second,
                     tzinfo=HELSINKI)
    return moved.astimezone(timezone.utc) + timedelta(minutes=moment.get("offset", 0))


def _local_day(now: datetime) -> date:
    return now.astimezone(HELSINKI).date()


def _day_ok(rule: dict, day: date) -> bool:
    days = rule.get("days")
    return not days or day.weekday() in days


def _in_window(now: datetime, start: str, end: str) -> bool:
    """Is the local time in [start, end) (across midnight when end <= start)?"""
    t = now.astimezone(HELSINKI).strftime("%H:%M")
    if start == end:
        return True
    return start <= t < end if start < end else (t >= start or t < end)


def between_on(rule: dict, now: datetime, sun: dict | None) -> bool | None:
    """Should the devices be on now? None without the sun time.

    With weekdays, a window counts on the day it starts (Friday's evening
    window runs past Saturday midnight).
    """
    today = _local_day(now)
    for day in (today - timedelta(days=1), today):
        start = moment_on(rule["start"], day, sun)
        end = moment_on(rule["end"], day, sun)
        if start is None or end is None:
            return None
        if end <= start:
            end = moment_on(rule["end"], day + timedelta(days=1), sun)
        if start <= now < end and _day_ok(rule, day):
            return True
    return False


def cheapest_plan(rule: dict, now: datetime, prices) -> tuple[bool, list[int], datetime | None]:
    """(on now, chosen period starts, window end) for a cheapest-hours rule.

    ``prices(day)`` returns (period start ts, c/kWh) pairs of a local day.
    Periods without a price come last; without any price the window's first
    hours are used.
    """
    today = _local_day(now)
    for day in (today - timedelta(days=1), today):
        start = moment_on({"type": "time", "at": rule["start"]}, day, None)
        end = moment_on({"type": "time", "at": rule["end"]}, day, None)
        if end <= start:
            end = moment_on({"type": "time", "at": rule["end"]}, day + timedelta(days=1), None)
        if start <= now < end:
            break
    else:
        return False, [], None
    if not _day_ok(rule, day):
        return False, [], None
    s, e = int(start.timestamp()), int(end.timestamp())
    known = {}
    for d in {_local_day(start), _local_day(end - timedelta(seconds=1))}:
        for ts, price in prices(d):
            known[ts] = price
    slots = list(range(s - s % PERIOD, e, PERIOD))
    need = min(len(slots), math.ceil(rule["hours"] * 3600 / PERIOD))
    order = sorted(slots, key=lambda ts: (known.get(ts) is None, known.get(ts) or 0, ts))
    chosen = sorted(order[:need])
    cur = int(now.timestamp())
    return cur - cur % PERIOD in chosen, chosen, end


def level_on(rule: dict, value: float | None, last: bool | None) -> bool | None:
    """On or off for a sensor reading; inside the dead band the last state holds
    (None when there is none yet or the sensor does not report)."""
    if value is None:
        return None
    if rule["mode"] == "below":
        if value < rule["on_at"]:
            return True
        if value > rule["off_at"]:
            return False
    else:
        if value > rule["on_at"]:
            return True
        if value < rule["off_at"]:
            return False
    return last


def price_now(now: datetime, prices) -> float | None:
    cur = int(now.timestamp())
    cur -= cur % PERIOD
    for ts, price in prices(_local_day(now)):
        if ts == cur:
            return price
    return None


# --- engine ----------------------------------------------------------------------------

def _anyone_home(states: dict) -> bool | None:
    people = [s.get("state") for e, s in states.items() if e.startswith("person.")]
    if not people:
        return None
    if "home" in people:
        return True
    if any(p in (None, "unknown", "unavailable") for p in people):
        return None
    return False


def _reading(device: dict | None) -> float | None:
    if device is None or not device.get("available"):
        return None
    v = device.get("value") if device.get("value") is not None else device.get("current")
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _check(cond: dict, states: dict, devices: dict) -> bool | None:
    """One "only when" condition: True, False or None (not known now)."""
    t = cond["type"]
    if t in ("home", "away"):
        home = _anyone_home(states)
        return None if home is None else home == (t == "home")
    device = devices.get(cond["key"])
    if t in ("above", "below"):
        v = _reading(device)
        return None if v is None else (v > cond["value"] if t == "above" else v < cond["value"])
    if device is None or not device.get("available") or device.get("on") is None:
        return None
    return device["on"] == (t == "is_on")


def _worst(results: list[dict]) -> dict:
    return max(results, key=lambda r: _WORST.index(r["state"]) if r["state"] in _WORST else 3)


class RuleEngine:
    """Runs one site's rules against its catalog.

    ``catalog`` gives ``devices_by_key()``, ``raw_states()`` and
    ``command(key, action, value, actor)`` and ``command_result(request_id)``;
    ``store`` keeps the rules and
    their last run; ``automation_on()`` is the site's Automaatio switch;
    ``prices(day)`` the site's prices with VAT as configured.
    """

    def __init__(self, site_id: str, catalog, store, *, automation_on, prices, now):
        self.site_id = site_id
        self._catalog = catalog
        self._store = store
        self._automation_on = automation_on
        self._prices = prices
        self._now = now
        self._seen: dict[tuple, object] = {}    # (rule id, trigger index) -> last value (edges)
        self._retry: dict[int, datetime] = {}   # rule id -> earliest retry of a failed change
        # rule id -> {"want", "body", "done": keys confirmed, "pending": {key: request id}}
        self._progress: dict[int, dict] = {}

    # -- what a rule wants --

    def conditions(self, body: dict, now: datetime, states: dict, devices: dict) -> bool | None:
        """Do the rule's conditions hold? None when one is not known now
        (and none is known to fail)."""
        if body["kind"] not in ("between", "cheapest") and not _day_ok(body, _local_day(now)):
            return False
        if "only" in body and not _in_window(now, body["only"]["start"], body["only"]["end"]):
            return False
        unknown = False
        if "max_price" in body:
            price = price_now(now, self._prices)
            if price is None:
                unknown = True
            elif price > body["max_price"]:
                return False
        for cond in body.get("when", []):
            ok = _check(cond, states, devices)
            if ok is False:
                return False
            unknown = unknown or ok is None
        return None if unknown else True

    def wanted(self, rule: dict, now: datetime, states: dict, devices: dict) -> bool | None:
        """On or off for a between, cheapest or level rule (None: leave as is)."""
        body = rule["rule"]
        if body["kind"] == "between":
            base = between_on(body, now, states.get("sun.sun"))
        elif body["kind"] == "cheapest":
            base = cheapest_plan(body, now, self._prices)[0]
        else:
            prog = self._progress.get(rule["id"])
            last = prog["want"] if prog else (
                None if rule["last_state"] is None else bool(rule["last_state"]))
            base = level_on(body, _reading(devices.get(body["sensor"])), last)
        if base is not True:
            return base
        return self.conditions(body, now, states, devices)

    def status(self, rule: dict, devices: dict, states: dict) -> dict:
        """What the page shows under a rule: its next or current state."""
        now = self._now()
        body = rule["rule"]
        out = {}
        if body["kind"] in STATE_KINDS:
            out["now_on"] = self.wanted(rule, now, states, devices)
            if body["kind"] == "between" and between_on(body, now, states.get("sun.sun")) is None:
                out["problem"] = "Aurinkotieto puuttuu (Home Assistantin sun-integraatio)."
            if body["kind"] == "cheapest":
                _, chosen, _ = cheapest_plan(body, now, self._prices)
                out["periods"] = [datetime.fromtimestamp(ts, timezone.utc).isoformat()
                                  for ts in chosen]
            if body["kind"] == "level":
                out["reading"] = _reading(devices.get(body["sensor"]))
        if rule.get("off_due"):
            out["off_due"] = rule["off_due"]
        if any(k not in devices for k in rule_keys(body)):
            out["problem"] = "Säännön laitetta ei löydy Home Assistantista."
        return out

    # -- running --

    def tick(self) -> list[dict]:
        """Run every enabled rule once; returns what was sent."""
        now = self._now()
        states = self._catalog.raw_states()
        devices = self._catalog.devices_by_key()
        automation = self._automation_on()
        sent = []
        for rule in self._store.rules(self.site_id):
            try:
                sent.extend(self._off_due(rule, now))
                if not rule["enabled"]:
                    for k in [k for k in self._seen if k[0] == rule["id"]]:
                        del self._seen[k]
                    continue
                sent.extend(self._run(rule, now, states, devices, automation))
            except Exception as err:   # one broken rule must not stop the others
                self._store.rule_ran(rule["id"], now, "failed", str(err))
        return sent

    def _send(self, rule: dict, key: str, action: str, value) -> dict:
        name = rule["rule"].get("name") or "Sääntö"
        try:
            result = self._catalog.command(key, action, value, f"Sääntö: {name}")
        except ValueError as err:
            return {"rule": rule["id"], "key": key, "state": "failed", "detail": str(err)}
        return {"rule": rule["id"], "key": key, "state": result["state"],
                "detail": result.get("detail") or "", "request_id": result.get("request_id")}

    def _record(self, rule: dict, now: datetime, results: list[dict]) -> None:
        if results:
            worst = _worst(results)
            self._store.rule_ran(rule["id"], now, worst["state"], worst.get("detail") or "")

    def _off_due(self, rule: dict, now: datetime) -> list[dict]:
        """An event rule's "off after": switch its devices off when due."""
        due = _parse(rule.get("off_due"))
        if due is None or now < due:
            return []
        self._store.rule_due(rule["id"], None)
        results = [self._send(rule, k, "power", False) for k in off_keys(rule["rule"])]
        self._record(rule, now, results)
        return results

    def _settle(self, rule: dict, now: datetime) -> bool:
        """Record finished commands of a state change; True while any is pending."""
        prog = self._progress.get(rule["id"])
        if prog is None or not prog["pending"]:
            return False
        finished = []
        for key, request_id in list(prog["pending"].items()):
            result = self._catalog.command_result(request_id) or {
                "state": "unknown", "detail": "Komennon tulosta ei enää löydy."}
            if result["state"] == "pending":
                continue
            del prog["pending"][key]
            finished.append((key, result))
        # An edited rule starts afresh, even if an old command finishes later.
        if prog["body"] != rule["rule"]:
            if not prog["pending"]:
                del self._progress[rule["id"]]
            return bool(prog["pending"])
        self._record(rule, now, [r for _, r in finished])
        for key, result in finished:
            if result["state"] == "confirmed":
                prog["done"].add(key)
            else:
                self._retry[rule["id"]] = now + RETRY
        if prog["pending"]:
            return True
        self._finish(rule, prog)
        return False

    def _finish(self, rule: dict, prog: dict) -> None:
        """Every device has the wanted state: remember it."""
        if set(prog["body"]["keys"]) <= prog["done"]:
            self._store.rule_state(rule["id"], prog["want"])
            rule["last_state"] = prog["want"]
            self._progress.pop(rule["id"], None)
            self._retry.pop(rule["id"], None)

    def _run(self, rule: dict, now: datetime, states: dict, devices: dict,
             automation: bool) -> list[dict]:
        body = rule["rule"]
        if body["kind"] in STATE_KINDS:
            return self._run_state(rule, now, states, devices, automation)
        fired = False
        for i, trigger in enumerate(body["triggers"]):   # every trigger keeps its baseline
            fired = self._fired(rule, i, trigger, now, states, devices) or fired
        if not fired or not automation:
            return []
        if self.conditions(body, now, states, devices) is not True:
            return []
        results = [self._send(rule, a["key"], a["action"], a["value"]) for a in body["actions"]]
        self._record(rule, now, results)
        if body.get("off_after") and off_keys(body):
            due = now + timedelta(minutes=body["off_after"])
            self._store.rule_due(rule["id"], due.isoformat())
        return results

    def _run_state(self, rule: dict, now: datetime, states: dict, devices: dict,
                   automation: bool) -> list[dict]:
        body = rule["rule"]
        if self._settle(rule, now):
            return []
        want = self.wanted(rule, now, states, devices)
        if want is None or not automation:
            return []
        prog = self._progress.get(rule["id"])
        if prog is not None and (prog["want"] != want or prog["body"] != body):
            self._progress.pop(rule["id"])
            self._retry.pop(rule["id"], None)
            prog = None
        if prog is None:
            if rule["last_state"] is not None and bool(rule["last_state"]) == want:
                return []
            prog = {"want": want, "body": body, "done": set(), "pending": {}}
        if now < self._retry.get(rule["id"], now):
            return []
        self._progress[rule["id"]] = prog
        results = []
        for key in body["keys"]:
            if key in prog["done"]:
                continue
            result = self._send(rule, key, "power", want)
            results.append(result)
            if result["state"] == "confirmed":
                prog["done"].add(key)
            elif result["state"] == "pending":
                prog["pending"][key] = result["request_id"]
            else:
                self._retry[rule["id"]] = now + RETRY
        self._record(rule, now, results)
        if not prog["pending"]:
            self._finish(rule, prog)
        return results

    def _fired(self, rule: dict, index: int, trigger: dict, now, states, devices) -> bool:
        t = trigger["type"]
        if t in ("time", "sun"):
            day = _local_day(now)
            used = _used_moments(rule["last_key"])
            for d in (day - timedelta(days=1), day):
                at = moment_on(trigger, d, states.get("sun.sun"))
                if at is None or not at <= now < at + GRACE:
                    continue
                mark = f"{index}:{d.isoformat()}"
                if mark in used:
                    return False
                keep = [m for m in used if m.partition(":")[2] >= (day - timedelta(days=1)).isoformat()]
                rule["last_key"] = " ".join(keep + [mark])
                self._store.rule_key(rule["id"], rule["last_key"])
                return True
            return False
        seen = (rule["id"], index)
        if t in ("arrive", "leave"):
            value = _anyone_home(states)
        elif t in ("price_below", "price_above"):
            price = price_now(now, self._prices)
            value = None if price is None else (price < trigger["value"] if t == "price_below"
                                                else price > trigger["value"])
        else:
            device = devices.get(trigger["key"])
            if device is None or not device.get("available"):
                self._seen.pop(seen, None)
                return False
            if t in ("above", "below"):
                v = _reading(device)
                value = None if v is None else (v > trigger["value"] if t == "above"
                                                else v < trigger["value"])
            else:
                value = device.get("on")
        previous = self._seen.get(seen)
        self._seen[seen] = value
        if value is None or previous is None:
            return False
        if t in ("turns_off", "leave"):
            return previous and not value
        return value and not previous


def _used_moments(last_key: str | None) -> list[str]:
    """Moments already run, as "trigger index:local day" (older rules: the day)."""
    out = []
    for part in (last_key or "").split():
        out.append(part if ":" in part else "0:" + part)
    return out


# --- storage -------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS device_rule (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id TEXT NOT NULL,
    body TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    position REAL,
    last_key TEXT,
    last_state INTEGER,
    last_run_utc TEXT,
    last_result TEXT,
    last_detail TEXT NOT NULL DEFAULT '',
    created_utc TEXT NOT NULL
);
"""
# Columns added after the first release (DeviceStore adds them when missing).
COLUMNS = (("off_due_utc", "TEXT"),)


def rows_to_rules(rows) -> list[dict]:
    return [{"id": r[0], "rule": upgrade(json.loads(r[1])), "enabled": bool(r[2]), "last_key": r[3],
             "last_state": r[4], "last_run": r[5], "last_result": r[6], "last_detail": r[7],
             "off_due": r[8]}
            for r in rows]
