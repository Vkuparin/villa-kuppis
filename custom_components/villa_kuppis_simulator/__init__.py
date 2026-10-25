"""Stand-in Mill heaters and ALPSTUGA sensors for the cottage Home Assistant.

Configured in YAML (deploy/cottage/packages/villa_kuppis_simulator.yaml).
Each configured room gets ``climate.sim_mill_<room>_<n>`` heaters and
``sensor.sim_alpstuga_<room>_{temperature,humidity,carbon_dioxide,pm25}``,
shaped like what the Matter integration gives for the real devices. The
room temperatures come from ``model.py`` and persist across restarts.

Services, to rehearse failures:

- ``villa_kuppis_simulator.set_available``: take entities off the network
  and back (a heater keeps heating on its own thermostat meanwhile);
- ``villa_kuppis_simulator.set_room_temperature``: jump a room's
  temperature (a door left open, a sauna evening).

Remove this integration and its package when the real devices are in.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
import random

import voluptuous as vol

from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.discovery import async_load_platform
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from . import model

_LOGGER = logging.getLogger(__name__)

DOMAIN = "villa_kuppis_simulator"
SIGNAL_UPDATE = f"{DOMAIN}_update"
TICK = timedelta(seconds=30)
SAVE_EVERY = timedelta(minutes=5)
STORAGE_VERSION = 1

HEATER_SCHEMA = vol.Schema({
    vol.Required("name"): cv.string,
    vol.Required("power_w"): vol.All(vol.Coerce(float), vol.Range(min=1, max=5000)),
    vol.Optional("min_temp", default=5.0): vol.Coerce(float),
    vol.Optional("max_temp", default=35.0): vol.Coerce(float),
    vol.Optional("step", default=0.5): vol.All(vol.Coerce(float), vol.Range(min=0.01)),
    vol.Optional("sensor_bias", default=0.5): vol.Coerce(float),
})
ROOM_SCHEMA = vol.Schema({
    vol.Required("name"): cv.string,
    vol.Required("loss_w_per_k"): vol.All(vol.Coerce(float), vol.Range(min=1)),
    vol.Required("capacity_kwh_per_k"): vol.All(vol.Coerce(float), vol.Range(min=0.01)),
    vol.Optional("start_temperature", default=6.0): vol.Coerce(float),
    vol.Required("heaters"): vol.All(cv.ensure_list, [HEATER_SCHEMA]),
})
CONFIG_SCHEMA = vol.Schema({
    DOMAIN: vol.Schema({
        vol.Optional("outdoor_entity"): cv.entity_id,
        vol.Optional("outdoor_temperature", default=0.0): vol.Coerce(float),
        vol.Optional("command_delay_seconds", default=2.0):
            vol.All(vol.Coerce(float), vol.Range(min=0, max=60)),
        vol.Required("rooms"): vol.Schema({cv.slug: ROOM_SCHEMA}),
    }),
}, extra=vol.ALLOW_EXTRA)

SET_AVAILABLE_SCHEMA = vol.Schema({
    vol.Required("entity_id"): cv.entity_ids,
    vol.Required("available"): cv.boolean,
})
SET_ROOM_SCHEMA = vol.Schema({
    vol.Required("room"): cv.slug,
    vol.Required("temperature"): vol.All(vol.Coerce(float), vol.Range(min=-30, max=40)),
})


def heater_entity_id(room: str, index: int) -> str:
    return f"climate.sim_mill_{room}_{index}"


def sensor_entity_id(room: str, kind: str) -> str:
    return f"sensor.sim_alpstuga_{room}_{kind}"


class Simulation:
    def __init__(self, hass: HomeAssistant, conf: dict):
        self.hass = hass
        self.outdoor_entity = conf.get("outdoor_entity")
        self.outdoor_default = conf["outdoor_temperature"]
        self.command_delay = conf["command_delay_seconds"]
        self.rng = random.Random()
        self.rooms: dict[str, model.RoomSim] = {}
        self.entities: dict[str, tuple] = {}     # entity_id -> (room, heater or sensor kind)
        for room_id, rc in conf["rooms"].items():
            heaters = [model.HeaterSim(
                key=heater_entity_id(room_id, i), name=hc["name"], power_w=hc["power_w"],
                min_temp=hc["min_temp"], max_temp=hc["max_temp"], step=hc["step"],
                sensor_bias=hc["sensor_bias"], setpoint=hc["min_temp"],
            ) for i, hc in enumerate(rc["heaters"], start=1)]
            room = model.RoomSim(room_id, rc["name"], rc["start_temperature"],
                                 rc["loss_w_per_k"], rc["capacity_kwh_per_k"] * 3.6e6, heaters)
            self.rooms[room_id] = room
            for heater in heaters:
                self.entities[heater.key] = (room, heater)
            for kind in ("temperature", "humidity", "carbon_dioxide", "pm25"):
                self.entities[sensor_entity_id(room_id, kind)] = (room, kind)
        self.updated_at: datetime = dt_util.utcnow()
        self.saved_at: datetime | None = None
        self.store = Store(hass, STORAGE_VERSION, DOMAIN)

    # -- persistence --

    async def async_load(self) -> None:
        data = await self.store.async_load() or {}
        for room_id, saved in (data.get("rooms") or {}).items():
            room = self.rooms.get(room_id)
            if room is None:
                continue
            room.temperature = float(saved.get("temperature", room.temperature))
            for heater in room.heaters:
                hs = (saved.get("heaters") or {}).get(heater.key) or {}
                heater.mode = hs.get("mode", heater.mode)
                heater.setpoint = float(hs.get("setpoint", heater.setpoint))
        saved_at = dt_util.parse_datetime(data.get("saved_at") or "")
        if saved_at is not None:
            # The real heaters kept heating while Home Assistant was down.
            gap = (dt_util.utcnow() - saved_at).total_seconds()
            outdoor = self.outdoor()
            for room in self.rooms.values():
                model.advance(room, outdoor, gap)

    def _data(self) -> dict:
        return {"saved_at": dt_util.utcnow().isoformat(), "rooms": {
            rid: {"temperature": round(r.temperature, 3), "heaters": {
                h.key: {"mode": h.mode, "setpoint": h.setpoint} for h in r.heaters}}
            for rid, r in self.rooms.items()}}

    async def async_save(self, *_args) -> None:
        self.saved_at = dt_util.utcnow()
        await self.store.async_save(self._data())

    # -- running --

    def outdoor(self) -> float:
        if self.outdoor_entity:
            state = self.hass.states.get(self.outdoor_entity)
            if state is not None:
                raw = (state.attributes.get("temperature")
                       if state.domain == "weather" else state.state)
                try:
                    return float(raw)
                except (TypeError, ValueError):
                    pass
        return self.outdoor_default

    async def async_tick(self, *_args) -> None:
        now = dt_util.utcnow()
        seconds = (now - self.updated_at).total_seconds()
        self.updated_at = now
        outdoor = self.outdoor()
        for room in self.rooms.values():
            model.advance(room, outdoor, seconds)
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)
        if self.saved_at is None or now - self.saved_at >= SAVE_EVERY:
            await self.async_save()

    def humidity(self, room: model.RoomSim) -> float:
        return model.humidity(room.temperature, self.outdoor())

    # -- heater commands, as the Matter integration would deliver them --

    def command(self, heater: model.HeaterSim, *, mode: str | None = None,
                setpoint: float | None = None) -> None:
        if not heater.available:
            raise HomeAssistantError(f"{heater.name} is not reachable")
        room = self.entities[heater.key][0]

        @callback
        def _apply(_now=None) -> None:
            if not heater.available:
                return   # the write was lost with the connection
            if mode is not None:
                heater.mode = mode
            if setpoint is not None:
                heater.setpoint = setpoint
            model.thermostat(room, heater)
            async_dispatcher_send(self.hass, SIGNAL_UPDATE)
            self.hass.async_create_task(self.async_save())

        if self.command_delay > 0:
            async_call_later(self.hass, self.command_delay, _apply)
        else:
            _apply()

    # -- services --

    async def async_set_available(self, call: ServiceCall) -> None:
        unknown = [e for e in call.data["entity_id"] if e not in self.entities]
        if unknown:
            raise ServiceValidationError(f"Not a simulated entity: {', '.join(unknown)}")
        for entity_id in call.data["entity_id"]:
            room, item = self.entities[entity_id]
            if isinstance(item, model.HeaterSim):
                item.available = call.data["available"]
            else:
                # One ALPSTUGA: all four readings go together.
                room.sensor_available = call.data["available"]
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)

    async def async_set_room_temperature(self, call: ServiceCall) -> None:
        room = self.rooms.get(call.data["room"])
        if room is None:
            raise ServiceValidationError(f"Unknown room {call.data['room']!r}")
        room.temperature = call.data["temperature"]
        for heater in room.heaters:
            model.thermostat(room, heater)
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)
        await self.async_save()


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    sim = Simulation(hass, config[DOMAIN])
    await sim.async_load()
    hass.data[DOMAIN] = sim
    hass.services.async_register(DOMAIN, "set_available", sim.async_set_available,
                                 schema=SET_AVAILABLE_SCHEMA)
    hass.services.async_register(DOMAIN, "set_room_temperature",
                                 sim.async_set_room_temperature, schema=SET_ROOM_SCHEMA)
    async_track_time_interval(hass, sim.async_tick, TICK)
    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, sim.async_save)
    for platform in (Platform.CLIMATE, Platform.SENSOR):
        hass.async_create_task(async_load_platform(hass, platform, DOMAIN, {}, config))
    _LOGGER.warning("Villa Kuppis simulator is running: the cottage heaters and "
                    "sensors are stand-ins, not real devices")
    return True
