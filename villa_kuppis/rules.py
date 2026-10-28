"""Villa Kuppis rules for Home Assistant devices (devices.py).

Rules are data made in the web page, so a new device joins a rule without a
release. Three kinds:

- ``between``: a device is on from a start moment to an end moment (a time
  of day, or sunrise or sunset with an offset), off otherwise. Festive
  lights from sunset to 23:00.
- ``cheapest``: a device is on in the cheapest 15-minute periods of a daily
  window, for the given number of hours, off otherwise.
- ``event``: when something happens (a time or sun moment, a sensor goes
  above or below a value, a device or binary sensor turns on or off,
  someone comes home or the last one leaves), if the optional time window
  and price limit allow, send one or more device actions.

``between`` and ``cheapest`` act only when the wanted state changes, so a
person's own change holds until the next change point. Every rule waits
while the site's Automaatio switch is off. Sun times come from Home
Assistant's ``sun.sun`` entity; presence from its ``person`` entities.
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
MAX_ACTIONS = 5
MAX_NAME = 60
KINDS = ("between", "cheapest", "event")
TRIGGERS = ("time", "sun", "above", "below", "turns_on", "turns_off", "arrive", "leave")
_HHMM = re.compile(r"([01]\d|2[0-3]):([0-5]\d)\Z")


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
        out.update(key=_key(rule.get("key"), "Laite"),
                   start=_moment(rule.get("start"), "Alku"), end=_moment(rule.get("end"), "Loppu"))
    elif kind == "cheapest":
        out.update(key=_key(rule.get("key"), "Laite"),
                   hours=_num(rule.get("hours"), "Tunnit", 0.25, 24),
                   start=_hhmm(rule.get("start"), "Ikkunan alku"),
                   end=_hhmm(rule.get("end"), "Ikkunan loppu"))
    else:
        trigger = rule.get("trigger")
        if not isinstance(trigger, dict) or trigger.get("type") not in TRIGGERS:
            raise RuleError("Valitse, mistä sääntö käynnistyy.")
        t = trigger["type"]
        if t in ("time", "sun"):
            clean = _moment(trigger, "Hetki")
        elif t in ("above", "below"):
            clean = {"type": t, "key": _key(trigger.get("key"), "Anturi"),
                     "value": _num(trigger.get("value"), "Raja", -1e6, 1e6)}
        elif t in ("turns_on", "turns_off"):
            clean = {"type": t, "key": _key(trigger.get("key"), "Laite")}
        else:
            clean = {"type": t}
        out["trigger"] = clean
        if rule.get("only"):
            only = rule["only"]
            if not isinstance(only, dict):
                raise RuleError("Aikaväli puuttuu.")
            out["only"] = {"start": _hhmm(only.get("start"), "Vain alkaen"),
                           "end": _hhmm(only.get("end"), "Vain asti")}
        if rule.get("max_price") is not None:
            out["max_price"] = _num(rule["max_price"], "Hintaraja (c/kWh)", -100, 1000)
        actions = rule.get("actions")
        if not isinstance(actions, list) or not 1 <= len(actions) <= MAX_ACTIONS:
            raise RuleError(f"Lisää 1–{MAX_ACTIONS} toimintoa.")
        out["actions"] = []
        for a in actions:
            if not isinstance(a, dict) or not isinstance(a.get("action"), str):
                raise RuleError("Toiminto puuttuu.")
            value = a.get("value")
            if not isinstance(value, (bool, int, float, str)) and value is not None:
                raise RuleError("Toiminnon arvo ei kelpaa.")
            out["actions"].append({"key": _key(a.get("key"), "Toiminnon laite"),
                                   "action": a["action"], "value": value})
    return out


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


def _in_window(now: datetime, start: str, end: str) -> bool:
    """Is the local time in [start, end) (across midnight when end <= start)?"""
    t = now.astimezone(HELSINKI).strftime("%H:%M")
    if start == end:
        return True
    return start <= t < end if start < end else (t >= start or t < end)


def between_on(rule: dict, now: datetime, sun: dict | None) -> bool | None:
    """Should the device be on now? None without the sun time."""
    today = _local_day(now)
    for day in (today - timedelta(days=1), today):
        start = moment_on(rule["start"], day, sun)
        end = moment_on(rule["end"], day, sun)
        if start is None or end is None:
            return None
        if end <= start:
            end = moment_on(rule["end"], day + timedelta(days=1), sun)
        if start <= now < end:
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
    return any(p == "home" for p in people)


class RuleEngine:
    """Runs one site's rules against its catalog.

    ``catalog`` gives ``devices_by_key()``, ``raw_states()`` and
    ``command(key, action, value, actor)``; ``store`` keeps the rules and
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
        self._seen: dict[int, object] = {}   # rule id -> last observed value (edges)
        self._retry: dict[int, datetime] = {}  # rule id -> earliest retry of a failed change

    def status(self, rule: dict, devices: dict, states: dict) -> dict:
        """What the page shows under a rule: its next or current state."""
        now = self._now()
        body = rule["rule"]
        sun = states.get("sun.sun")
        out = {}
        if body["kind"] == "between":
            on = between_on(body, now, sun)
            out["now_on"] = on
            if on is None:
                out["problem"] = "Aurinkotieto puuttuu (Home Assistantin sun-integraatio)."
        elif body["kind"] == "cheapest":
            on, chosen, _ = cheapest_plan(body, now, self._prices)
            out["now_on"] = on
            out["periods"] = [datetime.fromtimestamp(ts, timezone.utc).isoformat() for ts in chosen]
        keys = [body["key"]] if "key" in body else [a["key"] for a in body.get("actions", [])]
        if body.get("trigger", {}).get("key"):
            keys.append(body["trigger"]["key"])
        missing = [k for k in keys if k not in devices]
        if missing:
            out["problem"] = "Säännön laitetta ei löydy Home Assistantista."
        return out

    def tick(self) -> list[dict]:
        """Run every enabled rule once; returns what was sent."""
        now = self._now()
        states = self._catalog.raw_states()
        devices = self._catalog.devices_by_key()
        automation = self._automation_on()
        sent = []
        for rule in self._store.rules(self.site_id):
            if not rule["enabled"]:
                self._seen.pop(rule["id"], None)
                continue
            try:
                sent.extend(self._run(rule, now, states, devices, automation))
            except Exception as err:   # one broken rule must not stop the others
                self._store.rule_ran(rule["id"], now, "failed", str(err))
        return sent

    def _send(self, rule: dict, key: str, action: str, value, now) -> dict:
        name = rule["rule"].get("name") or "Sääntö"
        try:
            result = self._catalog.command(key, action, value, f"Sääntö: {name}")
        except ValueError as err:
            self._store.rule_ran(rule["id"], now, "failed", str(err))
            return {"rule": rule["id"], "key": key, "state": "failed", "detail": str(err)}
        self._store.rule_ran(rule["id"], now, result["state"], result.get("detail") or "")
        return {"rule": rule["id"], "key": key, "state": result["state"]}

    def _run(self, rule: dict, now: datetime, states: dict, devices: dict,
             automation: bool) -> list[dict]:
        body = rule["rule"]
        sun = states.get("sun.sun")
        if body["kind"] in ("between", "cheapest"):
            if body["kind"] == "between":
                want = between_on(body, now, sun)
            else:
                want = cheapest_plan(body, now, self._prices)[0]
            if want is None or not automation:
                return []
            if rule["last_state"] is not None and bool(rule["last_state"]) == want:
                return []
            if now < self._retry.get(rule["id"], now):
                return []
            result = self._send(rule, body["key"], "power", want, now)
            if result["state"] == "failed":
                self._retry[rule["id"]] = now + RETRY
            else:
                self._retry.pop(rule["id"], None)
                self._store.rule_state(rule["id"], want)
            return [result]
        fired = self._fired(rule, body["trigger"], now, states, devices, sun)
        if not fired or not automation:
            return []
        if "only" in body and not _in_window(now, body["only"]["start"], body["only"]["end"]):
            return []
        if "max_price" in body:
            price = price_now(now, self._prices)
            if price is None or price > body["max_price"]:
                return []
        return [self._send(rule, a["key"], a["action"], a["value"], now) for a in body["actions"]]

    def _fired(self, rule: dict, trigger: dict, now, states, devices, sun) -> bool:
        t = trigger["type"]
        if t in ("time", "sun"):
            day = _local_day(now)
            for d in (day - timedelta(days=1), day):
                at = moment_on(trigger, d, sun)
                if at is None or not at <= now < at + GRACE:
                    continue
                key = d.isoformat()
                if rule["last_key"] == key:
                    return False
                self._store.rule_key(rule["id"], key)
                return True
            return False
        if t in ("arrive", "leave"):
            value = _anyone_home(states)
        else:
            device = devices.get(trigger["key"])
            if device is None or not device.get("available"):
                return False
            if t in ("above", "below"):
                v = device.get("value") if device.get("value") is not None else device.get("current")
                value = None if v is None else (v > trigger["value"] if t == "above"
                                                else v < trigger["value"])
            else:
                value = device.get("on")
        previous = self._seen.get(rule["id"])
        self._seen[rule["id"]] = value
        if value is None or previous is None:
            return False
        if t in ("above", "below"):
            return value and not previous
        if t in ("turns_on", "arrive"):
            return value and not previous
        return previous and not value


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


def rows_to_rules(rows) -> list[dict]:
    return [{"id": r[0], "rule": json.loads(r[1]), "enabled": bool(r[2]), "last_key": r[3],
             "last_state": r[4], "last_run": r[5], "last_result": r[6], "last_detail": r[7]}
            for r in rows]
