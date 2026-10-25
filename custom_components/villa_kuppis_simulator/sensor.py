"""Stand-in IKEA ALPSTUGA readings: temperature, humidity, CO2 and PM2.5.

Like a Matter sensor, each reading is written when it changes by its
resolution, and at least every ``HEARTBEAT`` so ``last_reported`` stays
fresh. All four go unavailable together when the sensor drops off Thread.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    CONCENTRATION_PARTS_PER_MILLION,
    PERCENTAGE,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.util import dt as dt_util

from . import DOMAIN, SIGNAL_UPDATE, Simulation, model, sensor_entity_id
from .model import RoomSim

HEARTBEAT = timedelta(minutes=5)

# kind -> (device class, unit, resolution, label)
KINDS = {
    "temperature": (SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS, 0.1, "Temperature"),
    "humidity": (SensorDeviceClass.HUMIDITY, PERCENTAGE, 1.0, "Humidity"),
    "carbon_dioxide": (SensorDeviceClass.CO2, CONCENTRATION_PARTS_PER_MILLION, 10.0,
                       "Carbon dioxide"),
    "pm25": (SensorDeviceClass.PM25, CONCENTRATION_MICROGRAMS_PER_CUBIC_METER, 1.0, "PM2.5"),
}


async def async_setup_platform(hass: HomeAssistant, config, async_add_entities,
                               discovery_info=None) -> None:
    if discovery_info is None:
        return
    sim: Simulation = hass.data[DOMAIN]
    async_add_entities(SimulatedAlpstuga(sim, room, kind)
                       for room in sim.rooms.values() for kind in KINDS)


class SimulatedAlpstuga(SensorEntity):
    _attr_should_poll = False
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, sim: Simulation, room: RoomSim, kind: str):
        self._sim, self._room, self._kind = sim, room, kind
        device_class, unit, self._resolution, label = KINDS[kind]
        self.entity_id = sensor_entity_id(room.key, kind)
        self._attr_unique_id = f"{DOMAIN}_alpstuga_{room.key}_{kind}"
        self._attr_name = f"Sim ALPSTUGA {room.name} {label}"
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = unit
        self._attr_extra_state_attributes = {"simulated": True}
        self._value: float | None = None
        self._written_at: datetime | None = None
        self._was_available = True

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_UPDATE, self._update))
        self._value = self._measure()
        self._written_at = dt_util.utcnow()

    def _measure(self) -> float:
        room, sim = self._room, self._sim
        if self._kind == "temperature":
            raw = room.temperature
        elif self._kind == "humidity":
            raw = sim.humidity(room)
        elif self._kind == "carbon_dioxide":
            raw = model.co2(sim.rng)
        else:
            raw = model.pm25(sim.rng)
        return round(round(raw / self._resolution) * self._resolution, 1)

    @callback
    def _update(self) -> None:
        now = dt_util.utcnow()
        available = self._room.sensor_available
        if available != self._was_available:
            self._was_available = available
            if available:
                self._value = self._measure()
            self._written_at = now
            self.async_write_ha_state()
            return
        if not available:
            return
        value = self._measure()
        if self._kind in ("carbon_dioxide", "pm25") and abs(value - (self._value or 0)) < 2 * self._resolution:
            value = self._value   # small noise is not reported, like a real sensor
        if value != self._value or self._written_at is None or now - self._written_at >= HEARTBEAT:
            self._value, self._written_at = value, now
            self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return self._room.sensor_available

    @property
    def native_value(self) -> float | None:
        return self._value
