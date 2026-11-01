"""Cottage room controller: pure decision logic, no Home Assistant imports.

Card H2.2. Every minute, and whenever a sensor or heater reports, the
integration calls ``decide`` for each room with the room's latest inputs
and the controller's memory. ``decide`` returns the heater commands to
send and the room's status; the integration only executes them.

Modes, in precedence order:

- ``manual``: someone changed a heater (Google Home, the heater itself or
  Home Assistant's UI). The controller leaves the room alone until the
  hold ends or Villa Kuppis clears it, except for the frost guard below.
- ``fallback``: no valid target (Villa Kuppis, Tailscale or the internet
  is down, and the target and its schedule expired). The heaters' own
  thermostats hold the safe setpoint.
- ``thermostat``: the target is valid but the room sensor is missing,
  stale or implausible. The heaters' own thermostats hold the target.
- ``follow``: the room sensor is valid. Heaters are driven high
  (target + boost) below the target and low above it, with hysteresis and
  a minimum dwell. Their own thermostats still bound the temperature.

A heater switched off in Villa Kuppis (``enabled`` false) is kept off and
is not a manual change. Before it is turned off it gets the safe setpoint,
so a heater that ignores ``off`` does not keep heating to a high drive. In
fallback it holds the safe setpoint when ``fallback_heats_disabled`` is on.

Frost guard: whenever the room sensor reads below the safe setpoint, or
there is no valid room reading, no heater is left off or below the safe
setpoint: switched-off heaters and heaters a person turned down hold the
safe setpoint. The guard ends ``frost_margin`` above the safe setpoint, so
a forgotten switch or a manual hold never lets a room freeze.

Setpoints are clamped to each heater's range and rounded to its step. A
read-back within ``readback_tolerance`` of the sent value counts as the
heater's own rounding, not as a manual change, and is not resent.
A heater gets at most one command per ``min_command_interval`` and at most
``max_commands_per_hour``; a command is not repeated within
``confirm_grace`` while the heater catches up.

The integration starts with empty memory after a restart: a stored target
stays valid until its expiry, and the first report of each heater is the
baseline for manual detection, so a restart never looks like a manual
change.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

HEAT = "heat"
OFF = "off"


@dataclass(frozen=True)
class Settings:
    safe_setpoint: float = 5.0       # fallback; also the lowest room target
    low_setpoint: float = 5.0        # heater setpoint when driven low
    max_target: float = 25.0
    boost: float = 3.0               # high drive = target + boost
    hysteresis: float = 0.3
    min_dwell: timedelta = timedelta(minutes=10)
    sensor_max_age: timedelta = timedelta(minutes=30)
    sensor_min: float = -25.0        # plausible indoor range
    sensor_max: float = 40.0
    manual_hold: timedelta = timedelta(hours=4)
    min_command_interval: timedelta = timedelta(seconds=60)
    max_commands_per_hour: int = 12
    confirm_grace: timedelta = timedelta(seconds=120)
    default_step: float = 0.5
    fallback_heats_disabled: bool = True
    readback_tolerance: float = 1.0  # a heater may round what it was sent
    frost_margin: float = 1.0        # frost guard ends this far above safe_setpoint
    unconfirmed_after: int = 3       # sends without read-back before the status says so


@dataclass(frozen=True)
class Target:
    """The requested target; ``schedule`` holds later (start, target) steps.

    With a schedule the room keeps following Villa Kuppis's plan while
    Villa Kuppis is unreachable, until ``valid_until`` (card H3.3).
    """

    value: float
    revision: int
    valid_until: datetime
    schedule: tuple = ()             # ((start, target), ...), oldest first

    def value_at(self, now: datetime) -> float:
        value = self.value
        for start, target in self.schedule:
            if start > now:
                break
            value = target
        return value


@dataclass(frozen=True)
class Sensor:
    value: float | None
    reported_at: datetime | None


@dataclass(frozen=True)
class Heater:
    entity_id: str
    available: bool
    mode: str | None                 # "heat", "off", ...
    setpoint: float | None
    min_temp: float | None = None
    max_temp: float | None = None
    step: float | None = None
    enabled: bool = True             # False: switched off in Villa Kuppis


@dataclass
class HeaterMemory:
    seen: tuple | None = None        # (mode, setpoint) last observed
    sent_setpoint: float | None = None
    sent_mode: str | None = None
    sent_at: datetime | None = None
    sends: list = field(default_factory=list)
    unconfirmed: int = 0             # sends in a row the heater did not take


@dataclass
class RoomMemory:
    drive: str | None = None         # "high" or "low" in follow mode
    drive_since: datetime | None = None
    manual_until: datetime | None = None
    manual_by: str | None = None
    frost: bool = False              # frost guard on
    heaters: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Command:
    entity_id: str
    service: str                     # "set_temperature" or "set_hvac_mode"
    value: object


@dataclass(frozen=True)
class Decision:
    mode: str
    reason: str
    effective_target: float | None
    drive: str | None
    commands: tuple


def _close(a: float | None, b: float | None, tol: float) -> bool:
    return a is not None and b is not None and abs(a - b) <= tol


def heater_setpoint(heater: Heater, value: float, settings: Settings) -> float:
    """Clamp to the heater's range and round to its step."""
    lo = heater.min_temp if heater.min_temp is not None else -math.inf
    hi = heater.max_temp if heater.max_temp is not None else math.inf
    step = heater.step or settings.default_step
    value = min(max(value, lo), hi)
    base = heater.min_temp if heater.min_temp is not None else 0.0
    rounded = base + round((value - base) / step) * step
    if rounded > hi:
        rounded -= step
    return round(rounded, 2)


def sensor_valid(sensor: Sensor | None, now: datetime, settings: Settings) -> tuple[bool, str]:
    if sensor is None or sensor.value is None or not math.isfinite(sensor.value):
        return False, "no room temperature"
    if not settings.sensor_min <= sensor.value <= settings.sensor_max:
        return False, f"implausible room temperature {sensor.value:g} °C"
    if sensor.reported_at is None or now - sensor.reported_at > settings.sensor_max_age:
        return False, "room temperature is stale"
    return True, ""


def _tolerance(heater: Heater, settings: Settings) -> float:
    """Half a setpoint step: a setpoint this close is the one we want."""
    return (heater.step or settings.default_step) / 2 + 0.01


def _readback(heater: Heater, settings: Settings) -> float:
    """How far a heater may round what it was sent and still count as ours."""
    return max(_tolerance(heater, settings), settings.readback_tolerance)


def _detect_manual(heaters, memory: RoomMemory, settings: Settings, now: datetime) -> str | None:
    """Return the entity a person changed, updating each heater's baseline."""
    changed_by = None
    for heater in heaters:
        hm = memory.heaters.setdefault(heater.entity_id, HeaterMemory())
        if not heater.available:
            hm.seen = None   # a heater back from a power cut is a new baseline
            continue
        observed = (heater.mode, heater.setpoint)
        previous, hm.seen = hm.seen, observed
        if previous is None or previous == observed or not heater.enabled:
            continue
        mode_ours = heater.mode == previous[0] or heater.mode == hm.sent_mode
        setpoint_ours = (_close(heater.setpoint, previous[1], 1e-6)
                         or _close(heater.setpoint, hm.sent_setpoint, _readback(heater, settings)))
        if not (mode_ours and setpoint_ours):
            changed_by = heater.entity_id
    return changed_by


def _allowed(hm: HeaterMemory, now: datetime, settings: Settings) -> bool:
    hm.sends = [t for t in hm.sends if now - t < timedelta(hours=1)]
    if hm.sent_at is not None and now - hm.sent_at < settings.min_command_interval:
        return False
    return len(hm.sends) < settings.max_commands_per_hour


def _sent(hm: HeaterMemory, now: datetime) -> None:
    hm.sent_at = now
    hm.sends.append(now)
    hm.unconfirmed += 1


def _off_commands(heaters, memory: RoomMemory, settings: Settings,
                  now: datetime) -> list[Command]:
    """Turn the switched-off heaters off, once each, within the rate limits.

    A heater set above the safe setpoint gets the safe setpoint first, so
    one that ignores ``off`` holds only that.
    """
    out = []
    for heater in heaters:
        if heater.enabled or not heater.available:
            continue
        hm = memory.heaters.setdefault(heater.entity_id, HeaterMemory())
        if heater.mode == OFF:
            hm.unconfirmed = 0
            continue
        waiting = (hm.sent_at is not None and now - hm.sent_at < settings.confirm_grace
                   and hm.sent_mode == OFF)
        if waiting or not _allowed(hm, now, settings):
            continue
        safe = heater_setpoint(heater, settings.safe_setpoint, settings)
        if heater.setpoint is not None and heater.setpoint > safe + _tolerance(heater, settings):
            out.append(Command(heater.entity_id, "set_temperature", safe))
            hm.sent_setpoint = safe
        out.append(Command(heater.entity_id, "set_hvac_mode", OFF))
        hm.sent_mode = OFF
        _sent(hm, now)
    return out


def _commands(heaters, desired: float, memory: RoomMemory, settings: Settings,
              now: datetime, *, include_disabled: bool = False,
              raise_only: bool = False) -> list[Command]:
    """Bring each heater to heat at ``desired``.

    ``raise_only`` leaves a heater alone that already heats at or above
    ``desired`` (the frost guard must not turn a person's setpoint down).
    """
    out = []
    for heater in heaters:
        if not heater.available:
            continue
        if not heater.enabled and not include_disabled:
            continue
        hm = memory.heaters.setdefault(heater.entity_id, HeaterMemory())
        want = heater_setpoint(heater, desired, settings)
        tol = _tolerance(heater, settings)
        need_mode = heater.mode != HEAT
        if raise_only and heater.setpoint is not None and heater.setpoint >= want - tol:
            need_setpoint = False
        else:
            # The heater may round what it was sent: once it has taken our
            # value, its own rounding of it is not resent.
            need_setpoint = not (_close(heater.setpoint, want, tol)
                                 or (_close(hm.sent_setpoint, want, 1e-6)
                                     and _close(heater.setpoint, want, _readback(heater, settings))))
        if not (need_mode or need_setpoint):
            hm.unconfirmed = 0
            continue
        waiting = (hm.sent_at is not None and now - hm.sent_at < settings.confirm_grace
                   and (not need_mode or hm.sent_mode == HEAT)
                   and (not need_setpoint or _close(hm.sent_setpoint, want, 1e-6)))
        if waiting or not _allowed(hm, now, settings):
            continue
        if need_mode:
            out.append(Command(heater.entity_id, "set_hvac_mode", HEAT))
            hm.sent_mode = HEAT
        if need_setpoint:
            out.append(Command(heater.entity_id, "set_temperature", want))
            hm.sent_setpoint = want
        _sent(hm, now)
    return out


def _frost(sensor_ok: bool, room: float | None, memory: RoomMemory,
           settings: Settings) -> bool:
    """Frost guard: on below the safe setpoint or without a valid room
    reading, off again ``frost_margin`` above it."""
    if not sensor_ok or room is None:
        memory.frost = True
    elif room < settings.safe_setpoint:
        memory.frost = True
    elif room > settings.safe_setpoint + settings.frost_margin:
        memory.frost = False
    return memory.frost


def _disabled_commands(heaters, frost: bool, memory: RoomMemory, settings: Settings,
                       now: datetime) -> list[Command]:
    """Switched-off heaters: off, or the safe setpoint while the frost guard is on."""
    if frost:
        return _commands([h for h in heaters if not h.enabled], settings.safe_setpoint,
                         memory, settings, now, include_disabled=True)
    return _off_commands(heaters, memory, settings, now)


def _notes(heaters, memory: RoomMemory, settings: Settings) -> str:
    """Heaters that keep ignoring commands, for the status reason."""
    out = []
    for heater in heaters:
        hm = memory.heaters.get(heater.entity_id)
        if heater.available and hm is not None and hm.unconfirmed >= settings.unconfirmed_after:
            if hm.sent_mode == OFF and heater.mode != OFF:
                out.append(f"{heater.entity_id} does not take off")
            elif hm.sent_setpoint is not None:
                out.append(f"{heater.entity_id} does not take {hm.sent_setpoint:g} °C")
    return "; ".join(out)


def _decision(mode: str, reason: str, target, drive, cmds, heaters, memory: RoomMemory,
              settings: Settings, frost: bool) -> Decision:
    extra = [reason]
    if frost and any(not h.enabled for h in heaters):
        extra.append("frost guard heats switched-off heaters")
    notes = _notes(heaters, memory, settings)
    if notes:
        extra.append(notes)
    return Decision(mode, "; ".join(e for e in extra if e), target, drive, tuple(cmds))


def decide(now: datetime, target: Target | None, sensor: Sensor | None,
           heaters, memory: RoomMemory, settings: Settings = Settings()) -> Decision:
    heaters = list(heaters)
    changed = _detect_manual(heaters, memory, settings, now)
    if changed is not None:
        memory.manual_until = now + settings.manual_hold
        memory.manual_by = changed
        memory.drive = memory.drive_since = None
    ok, why = sensor_valid(sensor, now, settings)
    room = sensor.value if ok else None

    if memory.manual_until is not None and now < memory.manual_until:
        # A person's change stands, unless the room is freezing: then no
        # heater is left off or below the safe setpoint.
        cold = ok and _frost(ok, room, memory, settings)
        if cold:
            cmds = _commands(heaters, settings.safe_setpoint, memory, settings, now,
                             include_disabled=True, raise_only=True)
        else:
            cmds = _off_commands(heaters, memory, settings, now)
        reason = f"{memory.manual_by} changed by hand"
        if cold:
            reason += f"; room {room:g} °C, frost guard on"
        return _decision("manual", reason, None, None, cmds, heaters, memory, settings, False)
    memory.manual_until = memory.manual_by = None

    if target is None or now >= target.valid_until:
        memory.drive = memory.drive_since = None
        reason = "no target from Villa Kuppis" if target is None else "the target expired"
        heat_all = settings.fallback_heats_disabled
        frost = _frost(ok, room, memory, settings)
        cmds = _commands(heaters, settings.safe_setpoint, memory, settings, now,
                         include_disabled=heat_all)
        if not heat_all:
            cmds += _disabled_commands(heaters, frost, memory, settings, now)
        return _decision("fallback", reason, settings.safe_setpoint, None, cmds, heaters,
                         memory, settings, frost and not heat_all)

    value = min(max(target.value_at(now), settings.safe_setpoint), settings.max_target)
    frost = _frost(ok, room, memory, settings)
    if not ok:
        memory.drive = memory.drive_since = None
        cmds = _commands(heaters, value, memory, settings, now)
        cmds += _disabled_commands(heaters, frost, memory, settings, now)
        return _decision("thermostat", why, value, None, cmds, heaters, memory, settings, frost)

    if room < value - settings.hysteresis:
        want = "high"
    elif room > value + settings.hysteresis:
        want = "low"
    else:
        want = memory.drive or ("high" if room < value else "low")
    if (memory.drive is not None and want != memory.drive
            and now - memory.drive_since < settings.min_dwell):
        want = memory.drive
    if want != memory.drive:
        memory.drive, memory.drive_since = want, now
    desired = value + settings.boost if want == "high" else settings.low_setpoint
    cmds = _commands(heaters, desired, memory, settings, now)
    cmds += _disabled_commands(heaters, frost, memory, settings, now)
    return _decision("follow", f"room {room:g} °C, target {value:g} °C", value, want, cmds,
                     heaters, memory, settings, frost)
