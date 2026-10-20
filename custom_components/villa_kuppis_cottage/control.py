"""Cottage room controller: pure decision logic, no Home Assistant imports.

Card H2.2. Every minute, and whenever a sensor or heater reports, the
integration calls ``decide`` for each room with the room's latest inputs
and the controller's memory. ``decide`` returns the heater commands to
send and the room's status; the integration only executes them.

Modes, in precedence order:

- ``manual``: someone changed a heater (Google Home, the heater itself or
  Home Assistant's UI). The controller leaves the room alone until the
  hold ends or Villa Kuppis clears it.
- ``fallback``: no valid target (Villa Kuppis, Tailscale or the internet
  is down, or the target expired). The heaters' own thermostats hold the
  safe setpoint.
- ``thermostat``: the target is valid but the room sensor is missing,
  stale or implausible. The heaters' own thermostats hold the target.
- ``follow``: the room sensor is valid. Heaters are driven high
  (target + boost) below the target and low above it, with hysteresis and
  a minimum dwell. Their own thermostats still bound the temperature.

Setpoints are clamped to each heater's range and rounded to its step.
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
    safe_setpoint: float = 7.0       # fallback; also the lowest room target
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


@dataclass(frozen=True)
class Target:
    value: float
    revision: int
    valid_until: datetime


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


@dataclass
class HeaterMemory:
    seen: tuple | None = None        # (mode, setpoint) last observed
    sent_setpoint: float | None = None
    sent_mode: str | None = None
    sent_at: datetime | None = None
    sends: list = field(default_factory=list)


@dataclass
class RoomMemory:
    drive: str | None = None         # "high" or "low" in follow mode
    drive_since: datetime | None = None
    manual_until: datetime | None = None
    manual_by: str | None = None
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
        if previous is None or previous == observed:
            continue
        tol = (heater.step or settings.default_step) / 2 + 0.01
        mode_ours = heater.mode == previous[0] or heater.mode == hm.sent_mode
        setpoint_ours = (_close(heater.setpoint, previous[1], 1e-6)
                         or _close(heater.setpoint, hm.sent_setpoint, tol))
        if not (mode_ours and setpoint_ours):
            changed_by = heater.entity_id
    return changed_by


def _allowed(hm: HeaterMemory, now: datetime, settings: Settings) -> bool:
    hm.sends = [t for t in hm.sends if now - t < timedelta(hours=1)]
    if hm.sent_at is not None and now - hm.sent_at < settings.min_command_interval:
        return False
    return len(hm.sends) < settings.max_commands_per_hour


def _commands(heaters, desired: float, memory: RoomMemory, settings: Settings,
              now: datetime) -> list[Command]:
    out = []
    for heater in heaters:
        if not heater.available:
            continue
        hm = memory.heaters.setdefault(heater.entity_id, HeaterMemory())
        want = heater_setpoint(heater, desired, settings)
        tol = (heater.step or settings.default_step) / 2 + 0.01
        need_mode = heater.mode != HEAT
        need_setpoint = not _close(heater.setpoint, want, tol)
        if not (need_mode or need_setpoint):
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
        hm.sent_at = now
        hm.sends.append(now)
    return out


def decide(now: datetime, target: Target | None, sensor: Sensor | None,
           heaters, memory: RoomMemory, settings: Settings = Settings()) -> Decision:
    heaters = list(heaters)
    changed = _detect_manual(heaters, memory, settings, now)
    if changed is not None:
        memory.manual_until = now + settings.manual_hold
        memory.manual_by = changed
        memory.drive = memory.drive_since = None
    if memory.manual_until is not None and now < memory.manual_until:
        return Decision("manual", f"{memory.manual_by} changed by hand", None, None, ())
    memory.manual_until = memory.manual_by = None

    if target is None or now >= target.valid_until:
        memory.drive = memory.drive_since = None
        reason = "no target from Villa Kuppis" if target is None else "the target expired"
        cmds = _commands(heaters, settings.safe_setpoint, memory, settings, now)
        return Decision("fallback", reason, settings.safe_setpoint, None, tuple(cmds))

    value = min(max(target.value, settings.safe_setpoint), settings.max_target)
    ok, why = sensor_valid(sensor, now, settings)
    if not ok:
        memory.drive = memory.drive_since = None
        cmds = _commands(heaters, value, memory, settings, now)
        return Decision("thermostat", why, value, None, tuple(cmds))

    room = sensor.value
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
    return Decision("follow", f"room {room:g} °C, target {value:g} °C", value, want, tuple(cmds))
