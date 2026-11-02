"""Villa Kuppis cottage controller for Home Assistant (card H2.2).

Configured in YAML (see deploy/cottage/packages/villa_kuppis.yaml). Villa
Kuppis writes each room's target with ``villa_kuppis_cottage.set_target``
(target, increasing revision and a validity window), optionally with the
rest of its plan as a schedule; this integration follows the room sensor
locally, keeps to the schedule while Villa Kuppis is unreachable and falls
back to the safe setpoint when the target and its schedule expire.
``villa_kuppis_cottage.set_heater_enabled`` switches one heater off or back
on; a switched-off heater is kept off. A room with ``area`` also takes every
climate entity in that Home Assistant area as a heater (and its temperature
sensor when none is named), followed as devices move between areas;
``villa_kuppis_cottage.set_room_sensor`` picks the room's temperature sensor
from Villa Kuppis. ``villa_kuppis_cottage.heartbeat`` tells the cottage that
Villa Kuppis runs; when it stops, the cottage sends its own phone notices
(``alarm.py``, option ``alarm``). The decision logic is in
``control.py``; this file only reads states, calls the climate services
and keeps the targets and switched-off heaters across restarts.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging

import voluptuous as vol

from homeassistant.const import ATTR_TEMPERATURE, Platform
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
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

from . import alarm as cottage_alarm
from . import control

_LOGGER = logging.getLogger(__name__)

DOMAIN = "villa_kuppis_cottage"
SIGNAL_UPDATE = f"{DOMAIN}_update"
TICK = timedelta(seconds=60)
STORAGE_VERSION = 1
UNAVAILABLE = ("unavailable", "unknown")
SCHEDULE_STEP = timedelta(minutes=15)   # the last step's length
SCHEDULE_MAX = timedelta(hours=48)
COMMAND_TIMEOUT = 15.0                  # seconds for one heater command
# Revisions are Villa Kuppis's period start times (epoch seconds). A stored
# revision this far ahead of the cottage clock came from a wrong clock and
# must not lock out every later target.
REVISION_FUTURE_LIMIT = 86400

_DEFAULTS = control.Settings()

ROOM_SCHEMA = vol.All(vol.Schema({
    vol.Required("name"): cv.string,
    # A Home Assistant area (id or name): its climate entities are heaters
    # of the room, and its temperature sensor is used when none is named.
    vol.Optional("area"): cv.string,
    vol.Optional("sensor"): cv.entity_id,
    vol.Optional("heaters", default=list): vol.All(cv.ensure_list, [cv.entity_domain("climate")]),
    # Other readings of the room's ALPSTUGA: not used for control, only
    # published to Villa Kuppis with the room's devices.
    vol.Optional("humidity"): cv.entity_id,
    vol.Optional("co2"): cv.entity_id,
    vol.Optional("pm25"): cv.entity_id,
}), cv.has_at_least_one_key("area", "sensor"))

ALARM_SCHEMA = vol.Schema({
    vol.Optional("enabled", default=True): cv.boolean,
    # notify services of the cottage Home Assistant; empty: every phone
    # with the Home Assistant app (notify.mobile_app_*).
    vol.Optional("notify", default=list): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional("cold_below", default=3.0): vol.Coerce(float),
    vol.Optional("silent_minutes", default=60): vol.All(vol.Coerce(float), vol.Range(min=10)),
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
        vol.Optional("fallback_heats_disabled",
                     default=_DEFAULTS.fallback_heats_disabled): cv.boolean,
        vol.Optional("alarm"): ALARM_SCHEMA,
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
SET_ROOM_SENSOR_SCHEMA = vol.Schema({
    vol.Required("room"): cv.slug,
    # Left out: back to the sensor named in the package (or found in the area).
    vol.Optional("entity_id"): cv.entity_domain("sensor"),
})
SET_HEATER_ENABLED_SCHEMA = vol.Schema({
    vol.Required("entity_id"): cv.entity_domain("climate"),
    vol.Required("enabled"): cv.boolean,
})


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
        fallback_heats_disabled=conf["fallback_heats_disabled"],
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


def read_heater(entity_id: str, state: State | None, enabled: bool = True) -> control.Heater:
    if state is None or state.state in UNAVAILABLE:
        return control.Heater(entity_id, False, None, None, enabled=enabled)
    attrs = state.attributes
    return control.Heater(
        entity_id, True, state.state, _float(attrs.get(ATTR_TEMPERATURE)),
        _float(attrs.get("min_temp")), _float(attrs.get("max_temp")),
        _float(attrs.get("target_temp_step")), enabled,
    )


def area_entities(hass: HomeAssistant, area: str) -> list[er.RegistryEntry]:
    """The enabled, visible entities in an area (their own or their device's)."""
    areas = ar.async_get(hass)
    found = areas.async_get_area(area) or areas.async_get_area_by_name(area)
    if found is None:
        return []
    devices = dr.async_get(hass)
    out = []
    for entry in er.async_get(hass).entities.values():
        if entry.disabled_by or entry.hidden_by or entry.entity_category or entry.platform == DOMAIN:
            continue
        area_id = entry.area_id
        if area_id is None and entry.device_id:
            device = devices.async_get(entry.device_id)
            area_id = device.area_id if device else None
        if area_id == found.id:
            out.append(entry)
    return sorted(out, key=lambda e: e.entity_id)


def is_temperature(hass: HomeAssistant, entity_id: str) -> bool:
    state = hass.states.get(entity_id)
    return (state is not None and state.attributes.get("device_class") == "temperature"
            and state.attributes.get("unit_of_measurement") in ("°C", "°F"))


class Room:
    def __init__(self, room_id: str, conf: dict):
        self.room_id = room_id
        self.name = conf["name"]
        self.area = conf.get("area")
        self.named_sensor = conf.get("sensor")
        self.named_heaters = list(conf["heaters"])
        self.extra = {k: conf[k] for k in ("humidity", "co2", "pm25") if k in conf}
        self.chosen_sensor: str | None = None   # set_room_sensor
        self.sensor: str | None = self.named_sensor
        self.heaters = list(self.named_heaters)
        self.devices: dict = {}
        self.unsubscribe = None
        self._publish()
        self.memory = control.RoomMemory()
        self.disabled: set[str] = set()   # heaters switched off in Villa Kuppis
        self.target: control.Target | None = None
        self.decision: control.Decision | None = None
        self.room_temp: float | None = None
        self.fresh_temp: float | None = None     # for the alarm: a recent reading only

    def _publish(self) -> None:
        self.devices = {**({"temperature": self.sensor} if self.sensor else {}),
                        "heaters": self.heaters, **self.extra}

    def resolve(self, hass: HomeAssistant, taken: set[str]) -> bool:
        """Work out the sensor and heaters again; True when they changed.

        ``taken`` holds heaters named for other rooms: those stay there.
        """
        found = area_entities(hass, self.area) if self.area else []
        heaters = list(self.named_heaters) + [
            e.entity_id for e in found if e.domain == "climate"
            and e.entity_id not in self.named_heaters and e.entity_id not in taken]
        sensor = self.chosen_sensor or self.named_sensor or next(
            (e.entity_id for e in found if e.domain == "sensor"
             and is_temperature(hass, e.entity_id)), None)
        if heaters == self.heaters and sensor == self.sensor:
            return False
        self.heaters, self.sensor = heaters, sensor
        self.disabled &= set(heaters)
        self._publish()
        return True


class CottageController:
    def __init__(self, hass: HomeAssistant, conf: dict):
        self.hass = hass
        self.settings = _settings(conf)
        self.rooms = {rid: Room(rid, rc) for rid, rc in conf["rooms"].items()}
        self.store = Store(hass, STORAGE_VERSION, DOMAIN)
        self.context = Context()
        self.alarm_conf = ALARM_SCHEMA(conf.get("alarm") or {})
        self._unsent: set[str] = set()     # alarm titles no phone got yet (logged once)
        self.alarm = cottage_alarm.Alarm(cottage_alarm.AlarmSettings(
            cold_below=self.alarm_conf["cold_below"],
            silent=timedelta(minutes=self.alarm_conf["silent_minutes"])))

    async def async_load(self) -> None:
        data = await self.store.async_load() or {}
        self.alarm.heard = dt_util.parse_datetime(data.get("heard") or "")
        said = data.get("alarm") or {}
        self.alarm.silent_sent = bool(said.get("silent_sent"))
        self.alarm.cold = {name for name in said.get("cold") or () if isinstance(name, str)}
        for room_id, entity_id in (data.get("sensors") or {}).items():
            room = self.rooms.get(room_id)
            if room is not None:
                room.chosen_sensor = entity_id
        self.resolve()
        for room_id, entities in (data.get("disabled") or {}).items():
            room = self.rooms.get(room_id)
            if room is not None:
                room.disabled = {e for e in entities if e in room.heaters}
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
        }, "disabled": {rid: sorted(r.disabled) for rid, r in self.rooms.items() if r.disabled},
           "sensors": {rid: r.chosen_sensor for rid, r in self.rooms.items() if r.chosen_sensor},
           "heard": self.alarm.heard.isoformat() if self.alarm.heard else None,
           "alarm": {"silent_sent": self.alarm.silent_sent, "cold": sorted(self.alarm.cold)}})

    def resolve(self) -> list[Room]:
        """Rooms whose sensor or heaters changed (areas and chosen sensors)."""
        named = {h for room in self.rooms.values() for h in room.named_heaters}
        claimed: set[str] = set()   # an area heater goes to the first room that finds it
        changed = []
        for room in self.rooms.values():
            if room.resolve(self.hass, (named - set(room.named_heaters)) | claimed):
                changed.append(room)
            claimed |= set(room.heaters)
        return changed

    def track(self, room: Room) -> None:
        """Evaluate the room when its sensor or a heater changes."""
        if room.unsubscribe is not None:
            room.unsubscribe()

        @callback
        def _changed(_event):
            self.hass.async_create_task(self.async_evaluate(room))
        room.unsubscribe = async_track_state_change_event(
            self.hass, [e for e in (room.sensor, *room.heaters) if e], _changed)

    async def async_registry_changed(self, *_args) -> None:
        for room in self.resolve():
            _LOGGER.info("%s: sensor %s, heaters %s", room.name, room.sensor, room.heaters)
            self.track(room)
            await self._async_save()
            await self.async_evaluate(room)

    def _room(self, room_id: str) -> Room:
        room = self.rooms.get(room_id)
        if room is None:
            raise ServiceValidationError(f"Unknown room {room_id!r}")
        return room

    async def async_set_target(self, call: ServiceCall) -> None:
        room = self._room(call.data["room"])
        revision = call.data["revision"]
        now = dt_util.utcnow()
        if (room.target is not None and revision < room.target.revision
                and room.target.revision <= now.timestamp() + REVISION_FUTURE_LIMIT):
            raise ServiceValidationError(
                f"Revision {revision} is older than the stored revision {room.target.revision}")
        valid_until = now + timedelta(minutes=call.data["valid_minutes"])
        schedule = tuple(sorted((dt_util.as_utc(step["start"]), step["target"])
                                for step in call.data["schedule"]))
        schedule = tuple(step for step in schedule if step[0] < now + SCHEDULE_MAX)
        if schedule:
            valid_until = max(valid_until, schedule[-1][0] + SCHEDULE_STEP)
        room.target = control.Target(call.data["target"], revision, valid_until, schedule)
        await self._async_save()
        await self.async_evaluate(room)

    async def async_set_heater_enabled(self, call: ServiceCall) -> None:
        entity_id = call.data["entity_id"]
        room = next((r for r in self.rooms.values() if entity_id in r.heaters), None)
        if room is None:
            raise ServiceValidationError(f"{entity_id} is not a heater of any room")
        if call.data["enabled"]:
            room.disabled.discard(entity_id)
        else:
            room.disabled.add(entity_id)
        await self._async_save()
        await self.async_evaluate(room)

    async def async_set_room_sensor(self, call: ServiceCall) -> None:
        room = self._room(call.data["room"])
        entity_id = call.data.get("entity_id")
        if entity_id is not None and not is_temperature(self.hass, entity_id):
            raise ServiceValidationError(f"{entity_id} is not a temperature sensor")
        room.chosen_sensor = entity_id
        await self._async_save()
        await self.async_registry_changed()
        async_dispatcher_send(self.hass, SIGNAL_UPDATE, room.room_id)

    async def async_heartbeat(self, call: ServiceCall) -> None:
        """Villa Kuppis runs; the cottage alarm waits for its silence."""
        self.alarm.heartbeat(dt_util.utcnow())
        await self._async_save()
        await self.async_check_alarm()

    async def async_check_alarm(self) -> None:
        if not self.alarm_conf["enabled"]:
            return
        rooms = {room.name: room.fresh_temp for room in self.rooms.values()}
        plans = [room.target.valid_until for room in self.rooms.values()
                 if room.target is not None]
        said = False
        for key, title, message in self.alarm.check(
                dt_util.utcnow(), rooms, self.settings.safe_setpoint,
                max(plans) if plans else None):
            # A notice no phone got is offered again on the next check.
            if await self._async_notify(title, message):
                self.alarm.sent(key)
                said = True
        if said:
            await self._async_save()

    def _phones(self) -> list[str]:
        """The alarm's notify services: configured, or every phone with the app."""
        names = self.alarm_conf["notify"]
        if not names:
            services = self.hass.services.async_services_for_domain("notify")
            names = sorted(s for s in services if s.startswith("mobile_app_"))
        return list(names)

    def alarm_status(self) -> dict:
        """For the room sensors: is the alarm on, which phones it reaches and
        when Villa Kuppis was last heard (to 5 minutes, like ``reported``)."""
        heard = self.alarm.heard
        if heard is not None:
            heard = dt_util.utc_from_timestamp(int(heard.timestamp()) // 300 * 300)
        return {"enabled": self.alarm_conf["enabled"],
                "phones": [n.removeprefix("mobile_app_") for n in self._phones()],
                "silent_minutes": self.alarm_conf["silent_minutes"],
                "heard": heard.isoformat() if heard else None}

    async def _async_notify(self, title: str, message: str) -> int:
        """Send to every phone; returns how many got it."""
        names = self._phones()
        if not names:
            if title not in self._unsent:
                _LOGGER.warning("Alarm not sent, no phone has the Home Assistant app: %s", title)
            self._unsent.add(title)
            return 0
        sent = 0
        for name in names:
            try:
                await self.hass.services.async_call("notify", name,
                                                    {"title": title, "message": message},
                                                    blocking=True, context=self.context)
                sent += 1
            except Exception as err:   # one phone must not stop the others
                if title not in self._unsent:
                    _LOGGER.warning("Alarm to %s failed: %s", name, err or type(err).__name__)
        if sent:
            self._unsent.discard(title)
            _LOGGER.warning("Alarm sent: %s", title)
        else:
            self._unsent.add(title)   # retried every minute; logged once
        return sent

    async def async_clear_manual(self, call: ServiceCall) -> None:
        room = self._room(call.data["room"])
        room.memory.manual_until = None
        await self.async_evaluate(room)

    async def async_evaluate(self, room: Room) -> None:
        states = self.hass.states
        sensor = read_sensor(states.get(room.sensor)) if room.sensor else None
        heaters = [read_heater(eid, states.get(eid), eid not in room.disabled)
                   for eid in room.heaters]
        room.room_temp = sensor.value if sensor else None
        now = dt_util.utcnow()
        room.fresh_temp = (sensor.value if sensor and sensor.reported_at is not None
                           and now - sensor.reported_at <= self.settings.sensor_max_age
                           else None)
        decision = control.decide(now, room.target, sensor, heaters,
                                  room.memory, self.settings)
        if room.decision is None or decision.mode != room.decision.mode:
            _LOGGER.info("%s: %s (%s)", room.name, decision.mode, decision.reason)
        room.decision = decision
        async_dispatcher_send(self.hass, SIGNAL_UPDATE, room.room_id)
        for cmd in decision.commands:
            data = {"entity_id": cmd.entity_id}
            data["hvac_mode" if cmd.service == "set_hvac_mode" else ATTR_TEMPERATURE] = cmd.value
            try:
                # Blocking, in order: a refused write is seen here (and in the
                # status after repeated tries), and a safe setpoint lands
                # before the off that follows it.
                async with asyncio.timeout(COMMAND_TIMEOUT):
                    await self.hass.services.async_call("climate", cmd.service, data,
                                                        blocking=True, context=self.context)
            except Exception as err:   # one heater must not stop the others
                _LOGGER.warning("%s: %s %s %s failed: %s", room.name, cmd.service,
                                cmd.entity_id, cmd.value, err or type(err).__name__)

    async def async_evaluate_all(self, *_args) -> None:
        for room in self.rooms.values():
            await self.async_evaluate(room)
        await self.async_check_alarm()


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    conf = config[DOMAIN]
    controller = CottageController(hass, conf)
    await controller.async_load()
    hass.data[DOMAIN] = controller

    hass.services.async_register(DOMAIN, "set_target", controller.async_set_target,
                                 schema=SET_TARGET_SCHEMA)
    hass.services.async_register(DOMAIN, "clear_manual", controller.async_clear_manual,
                                 schema=CLEAR_MANUAL_SCHEMA)
    hass.services.async_register(DOMAIN, "set_heater_enabled",
                                 controller.async_set_heater_enabled,
                                 schema=SET_HEATER_ENABLED_SCHEMA)
    hass.services.async_register(DOMAIN, "set_room_sensor", controller.async_set_room_sensor,
                                 schema=SET_ROOM_SENSOR_SCHEMA)
    hass.services.async_register(DOMAIN, "heartbeat", controller.async_heartbeat,
                                 schema=vol.Schema({}))

    for room in controller.rooms.values():
        controller.track(room)
    async_track_time_interval(hass, controller.async_evaluate_all, TICK)
    if any(room.area for room in controller.rooms.values()):
        @callback
        def _registry(_event):
            hass.async_create_task(controller.async_registry_changed())
        for event in (er.EVENT_ENTITY_REGISTRY_UPDATED, dr.EVENT_DEVICE_REGISTRY_UPDATED,
                      ar.EVENT_AREA_REGISTRY_UPDATED):
            hass.bus.async_listen(event, _registry)

    hass.async_create_task(async_load_platform(hass, Platform.SENSOR, DOMAIN, {}, config))
    hass.async_create_task(controller.async_evaluate_all())
    return True
