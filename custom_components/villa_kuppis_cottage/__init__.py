"""Villa Kuppis cottage controller for Home Assistant (card H2.2).

Configured in YAML (see deploy/cottage/packages/villa_kuppis.yaml). Villa
Kuppis writes each room's target with ``villa_kuppis_cottage.set_target``
(target, increasing revision and a validity window), optionally with the
rest of its plan as a schedule; this integration follows the room sensor
locally, keeps to the schedule while Villa Kuppis is unreachable and falls
back to the safe setpoint when the target and its schedule expire. The decision logic is in ``control.py``; this file only
reads states, calls the climate services and keeps the targets across
restarts.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging

import voluptuous as vol

from homeassistant.const import ATTR_TEMPERATURE, Platform
from homeassistant.core import Context, HomeAssistant, ServiceCall, State, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.discovery import async_load_platform
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from . import control

_LOGGER = logging.getLogger(__name__)

DOMAIN = "villa_kuppis_cottage"
SIGNAL_UPDATE = f"{DOMAIN}_update"
TICK = timedelta(seconds=60)
STORAGE_VERSION = 1
UNAVAILABLE = ("unavailable", "unknown")
SCHEDULE_STEP = timedelta(minutes=15)   # the last step's length
SCHEDULE_MAX = timedelta(hours=48)

_DEFAULTS = control.Settings()

ROOM_SCHEMA = vol.Schema({
    vol.Required("name"): cv.string,
    vol.Required("sensor"): cv.entity_id,
    vol.Required("heaters"): vol.All(cv.ensure_list, [cv.entity_domain("climate")]),
    # Other readings of the room's ALPSTUGA: not used for control, only
    # published to Villa Kuppis with the room's devices.
    vol.Optional("humidity"): cv.entity_id,
    vol.Optional("co2"): cv.entity_id,
    vol.Optional("pm25"): cv.entity_id,
})

CONFIG_SCHEMA = vol.Schema({
    DOMAIN: vol.Schema({
        vol.Optional("safe_setpoint", default=_DEFAULTS.safe_setpoint): vol.Coerce(float),
        vol.Optional("low_setpoint", default=_DEFAULTS.low_setpoint): vol.Coerce(float),
        vol.Optional("max_target", default=_DEFAULTS.max_target): vol.Coerce(float),
        vol.Optional("boost", default=_DEFAULTS.boost): vol.Coerce(float),
        vol.Optional("hysteresis", default=_DEFAULTS.hysteresis): vol.Coerce(float),
        vol.Optional("min_dwell_minutes", default=10): vol.Coerce(float),
        vol.Optional("sensor_max_age_minutes", default=30): vol.Coerce(float),
        vol.Optional("manual_hold_minutes", default=240): vol.Coerce(float),
        vol.Optional("max_commands_per_hour",
                     default=_DEFAULTS.max_commands_per_hour): cv.positive_int,
        vol.Required("rooms"): vol.Schema({cv.slug: ROOM_SCHEMA}),
    }),
}, extra=vol.ALLOW_EXTRA)

SET_TARGET_SCHEMA = vol.Schema({
    vol.Required("room"): cv.slug,
    vol.Required("target"): vol.All(vol.Coerce(float), vol.Range(min=0, max=30)),
    vol.Required("revision"): vol.All(vol.Coerce(int), vol.Range(min=0)),
    vol.Optional("valid_minutes", default=45): vol.All(vol.Coerce(float), vol.Range(min=1, max=1440)),
    vol.Optional("schedule", default=list): vol.All(cv.ensure_list, vol.Length(max=400), [vol.Schema({
        vol.Required("start"): cv.datetime,
        vol.Required("target"): vol.All(vol.Coerce(float), vol.Range(min=0, max=30)),
    })]),
})
CLEAR_MANUAL_SCHEMA = vol.Schema({vol.Required("room"): cv.slug})


def _settings(conf: dict) -> control.Settings:
    return control.Settings(
        safe_setpoint=conf["safe_setpoint"],
        low_setpoint=conf["low_setpoint"],
        max_target=conf["max_target"],
        boost=conf["boost"],
        hysteresis=conf["hysteresis"],
        min_dwell=timedelta(minutes=conf["min_dwell_minutes"]),
        sensor_max_age=timedelta(minutes=conf["sensor_max_age_minutes"]),
        manual_hold=timedelta(minutes=conf["manual_hold_minutes"]),
        max_commands_per_hour=conf["max_commands_per_hour"],
    )


def _float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _reported(state: State) -> datetime:
    return getattr(state, "last_reported", None) or state.last_updated


def read_sensor(state: State | None) -> control.Sensor | None:
    if state is None or state.state in UNAVAILABLE:
        return None
    return control.Sensor(_float(state.state), _reported(state))


def read_heater(entity_id: str, state: State | None) -> control.Heater:
    if state is None or state.state in UNAVAILABLE:
        return control.Heater(entity_id, False, None, None)
    attrs = state.attributes
    return control.Heater(
        entity_id, True, state.state, _float(attrs.get(ATTR_TEMPERATURE)),
        _float(attrs.get("min_temp")), _float(attrs.get("max_temp")),
        _float(attrs.get("target_temp_step")),
    )


class Room:
    def __init__(self, room_id: str, conf: dict):
        self.room_id = room_id
        self.name = conf["name"]
        self.sensor = conf["sensor"]
        self.heaters = list(conf["heaters"])
        self.devices = {"temperature": self.sensor, "heaters": self.heaters,
                        **{k: conf[k] for k in ("humidity", "co2", "pm25") if k in conf}}
        self.memory = control.RoomMemory()
        self.target: control.Target | None = None
        self.decision: control.Decision | None = None
        self.room_temp: float | None = None


class CottageController:
    def __init__(self, hass: HomeAssistant, conf: dict):
        self.hass = hass
        self.settings = _settings(conf)
        self.rooms = {rid: Room(rid, rc) for rid, rc in conf["rooms"].items()}
        self.store = Store(hass, STORAGE_VERSION, DOMAIN)
        self.context = Context()

    async def async_load(self) -> None:
        data = await self.store.async_load() or {}
        for room_id, saved in (data.get("targets") or {}).items():
            room = self.rooms.get(room_id)
            until = dt_util.parse_datetime(saved.get("valid_until") or "")
            if room is not None and until is not None:
                schedule = tuple((dt_util.parse_datetime(start), float(value))
                                 for start, value in saved.get("schedule") or ())
                room.target = control.Target(float(saved["value"]), int(saved["revision"]),
                                             until, schedule)

    async def _async_save(self) -> None:
        await self.store.async_save({"targets": {
            rid: {"value": r.target.value, "revision": r.target.revision,
                  "valid_until": r.target.valid_until.isoformat(),
                  "schedule": [[start.isoformat(), value] for start, value in r.target.schedule]}
            for rid, r in self.rooms.items() if r.target is not None
        }})

    def _room(self, room_id: str) -> Room:
        room = self.rooms.get(room_id)
        if room is None:
            raise ServiceValidationError(f"Unknown room {room_id!r}")
        return room

    async def async_set_target(self, call: ServiceCall) -> None:
        room = self._room(call.data["room"])
        revision = call.data["revision"]
        if room.target is not None and revision < room.target.revision:
            raise ServiceValidationError(
                f"Revision {revision} is older than the stored revision {room.target.revision}")
        now = dt_util.utcnow()
        valid_until = now + timedelta(minutes=call.data["valid_minutes"])
        schedule = tuple(sorted((dt_util.as_utc(step["start"]), step["target"])
                                for step in call.data["schedule"]))
        schedule = tuple(step for step in schedule if step[0] < now + SCHEDULE_MAX)
        if schedule:
            valid_until = max(valid_until, schedule[-1][0] + SCHEDULE_STEP)
        room.target = control.Target(call.data["target"], revision, valid_until, schedule)
        await self._async_save()
        await self.async_evaluate(room)

    async def async_clear_manual(self, call: ServiceCall) -> None:
        room = self._room(call.data["room"])
        room.memory.manual_until = None
        await self.async_evaluate(room)

    async def async_evaluate(self, room: Room) -> None:
        states = self.hass.states
        sensor = read_sensor(states.get(room.sensor))
        heaters = [read_heater(eid, states.get(eid)) for eid in room.heaters]
        room.room_temp = sensor.value if sensor else None
        decision = control.decide(dt_util.utcnow(), room.target, sensor, heaters,
                                  room.memory, self.settings)
        if room.decision is None or decision.mode != room.decision.mode:
            _LOGGER.info("%s: %s (%s)", room.name, decision.mode, decision.reason)
        room.decision = decision
        async_dispatcher_send(self.hass, SIGNAL_UPDATE, room.room_id)
        for cmd in decision.commands:
            data = {"entity_id": cmd.entity_id}
            data["hvac_mode" if cmd.service == "set_hvac_mode" else ATTR_TEMPERATURE] = cmd.value
            try:
                await self.hass.services.async_call("climate", cmd.service, data,
                                                    blocking=False, context=self.context)
            except Exception as err:   # one heater must not stop the others
                _LOGGER.warning("%s: %s %s failed: %s", room.name, cmd.service,
                                cmd.entity_id, err)

    async def async_evaluate_all(self, *_args) -> None:
        for room in self.rooms.values():
            await self.async_evaluate(room)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    conf = config[DOMAIN]
    controller = CottageController(hass, conf)
    await controller.async_load()
    hass.data[DOMAIN] = controller

    hass.services.async_register(DOMAIN, "set_target", controller.async_set_target,
                                 schema=SET_TARGET_SCHEMA)
    hass.services.async_register(DOMAIN, "clear_manual", controller.async_clear_manual,
                                 schema=CLEAR_MANUAL_SCHEMA)

    for room in controller.rooms.values():
        @callback
        def _changed(_event, room=room):
            hass.async_create_task(controller.async_evaluate(room))
        async_track_state_change_event(hass, [room.sensor, *room.heaters], _changed)
    async_track_time_interval(hass, controller.async_evaluate_all, TICK)

    hass.async_create_task(async_load_platform(hass, Platform.SENSOR, DOMAIN, {}, config))
    hass.async_create_task(controller.async_evaluate_all())
    return True
