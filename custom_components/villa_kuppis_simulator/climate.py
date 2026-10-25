"""Stand-in Mill Gen4 heaters, shaped like the Matter integration's climate.

Modes ``off`` and ``heat``, the setpoint range and step from the package,
``current_temperature`` from the heater's own sensor and ``hvac_action``
from its thermostat. A command shows up after ``command_delay_seconds``;
an unreachable heater refuses commands but keeps heating.
"""

from __future__ import annotations

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from . import DOMAIN, SIGNAL_UPDATE, Simulation
from .model import HeaterSim, RoomSim


async def async_setup_platform(hass: HomeAssistant, config, async_add_entities,
                               discovery_info=None) -> None:
    if discovery_info is None:
        return
    sim: Simulation = hass.data[DOMAIN]
    async_add_entities(SimulatedMill(sim, room, heater)
                       for room in sim.rooms.values() for heater in room.heaters)


class SimulatedMill(ClimateEntity):
    _attr_should_poll = False
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = (ClimateEntityFeature.TARGET_TEMPERATURE
                                | ClimateEntityFeature.TURN_ON
                                | ClimateEntityFeature.TURN_OFF)
    _enable_turn_on_off_backwards_compatibility = False

    def __init__(self, sim: Simulation, room: RoomSim, heater: HeaterSim):
        self._sim, self._room, self._heater = sim, room, heater
        self.entity_id = heater.key
        self._attr_unique_id = f"{DOMAIN}_{heater.key.partition('.')[2]}"
        self._attr_name = f"Sim {heater.name}"
        self._attr_min_temp = heater.min_temp
        self._attr_max_temp = heater.max_temp
        self._attr_target_temperature_step = heater.step

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_UPDATE, self._update))

    @callback
    def _update(self) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return self._heater.available

    @property
    def hvac_mode(self) -> HVACMode:
        return HVACMode.HEAT if self._heater.mode == "heat" else HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction:
        if self._heater.mode != "heat":
            return HVACAction.OFF
        return HVACAction.HEATING if self._heater.heating else HVACAction.IDLE

    @property
    def target_temperature(self) -> float:
        return self._heater.setpoint

    @property
    def current_temperature(self) -> float:
        return round(self._room.heater_reading(self._heater), 1)

    @property
    def extra_state_attributes(self) -> dict:
        return {"simulated": True, "power_w": self._heater.power_w}

    async def async_set_temperature(self, **kwargs) -> None:
        mode = kwargs.get(ATTR_HVAC_MODE)
        self._sim.command(self._heater, setpoint=kwargs.get(ATTR_TEMPERATURE),
                          mode=str(mode) if mode is not None else None)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        self._sim.command(self._heater, mode=str(hvac_mode))

    async def async_turn_on(self) -> None:
        self._sim.command(self._heater, mode="heat")

    async def async_turn_off(self) -> None:
        self._sim.command(self._heater, mode="off")
