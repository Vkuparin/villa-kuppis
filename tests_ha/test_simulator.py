"""The stand-in cottage devices inside a real Home Assistant core, driven by
the cottage controller as the live cottage-haos runs them before the real
Mill heaters and ALPSTUGA sensors are in.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import async_fire_time_changed

SIM = "villa_kuppis_simulator"
COTTAGE = "villa_kuppis_cottage"
SIM_CONFIG = {SIM: {
    "outdoor_temperature": -10,
    "command_delay_seconds": 0,
    "rooms": {
        "large": {"name": "Iso huone", "loss_w_per_k": 60, "capacity_kwh_per_k": 0.8,
                  "start_temperature": 6,
                  "heaters": [{"name": "Mill 1050 W (1)", "power_w": 1050},
                              {"name": "Mill 1050 W (2)", "power_w": 1050},
                              {"name": "Mill 500 W", "power_w": 500}]},
        "small": {"name": "Pieni huone", "loss_w_per_k": 22, "capacity_kwh_per_k": 0.3,
                  "start_temperature": 6,
                  "heaters": [{"name": "Mill 1050 W", "power_w": 1050}]},
    },
}}
COTTAGE_CONFIG = {COTTAGE: {"rooms": {
    "large": {"name": "Iso huone", "sensor": "sensor.sim_alpstuga_large_temperature",
              "humidity": "sensor.sim_alpstuga_large_humidity",
              "heaters": ["climate.sim_mill_large_1", "climate.sim_mill_large_2",
                          "climate.sim_mill_large_3"]},
    "small": {"name": "Pieni huone", "sensor": "sensor.sim_alpstuga_small_temperature",
              "heaters": ["climate.sim_mill_small_1"]},
}}}


async def setup(hass, cottage=True):
    assert await async_setup_component(hass, SIM, SIM_CONFIG)
    await hass.async_block_till_done()
    if cottage:
        assert await async_setup_component(hass, COTTAGE, COTTAGE_CONFIG)
    await hass.async_block_till_done()


async def run(hass, freezer, minutes):
    for _ in range(minutes * 2):
        freezer.tick(timedelta(seconds=30))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()


async def test_entities_look_like_the_matter_devices(hass: HomeAssistant):
    await setup(hass, cottage=False)
    heater = hass.states.get("climate.sim_mill_large_3")
    assert heater.state == "heat"
    assert heater.attributes["hvac_modes"] == ["off", "heat"]
    assert heater.attributes["min_temp"] == 5.0
    assert heater.attributes["max_temp"] == 35.0
    assert heater.attributes["target_temp_step"] == 0.5
    assert heater.attributes["temperature"] == 5.0
    assert heater.attributes["simulated"] is True
    for kind, unit in (("temperature", "°C"), ("humidity", "%"),
                       ("carbon_dioxide", "ppm"), ("pm25", "μg/m³")):
        state = hass.states.get(f"sensor.sim_alpstuga_small_{kind}")
        assert state is not None, kind
        assert state.attributes["unit_of_measurement"] == unit
    assert hass.states.get("sensor.sim_alpstuga_small_temperature").state == "6.0"


async def test_controller_heats_the_stand_in_room(hass, freezer):
    await setup(hass)
    await hass.services.async_call(COTTAGE, "set_target", {
        "room": "large", "target": 18, "revision": 1, "valid_minutes": 1440}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get("climate.sim_mill_large_1").attributes["temperature"] == 21.0
    assert hass.states.get("climate.sim_mill_large_1").attributes["hvac_action"] == "heating"
    await run(hass, freezer, 12 * 60)
    room = float(hass.states.get("sensor.sim_alpstuga_large_temperature").state)
    assert 17.4 <= room <= 18.6
    status = hass.states.get("sensor.villa_kuppis_large_target")
    assert status.attributes["mode"] == "follow"
    assert status.attributes["devices"]["humidity"] == "sensor.sim_alpstuga_large_humidity"
    # Without a target the small room holds the 5 °C fallback.
    assert hass.states.get("sensor.villa_kuppis_small_target").attributes["mode"] == "fallback"
    small = float(hass.states.get("sensor.sim_alpstuga_small_temperature").state)
    assert 4.3 <= small <= 6.0


async def test_dropouts(hass, freezer):
    await setup(hass)
    await hass.services.async_call(SIM, "set_available", {
        "entity_id": ["sensor.sim_alpstuga_large_temperature", "climate.sim_mill_large_2"],
        "available": False}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.sim_alpstuga_large_humidity").state == "unavailable"
    assert hass.states.get("climate.sim_mill_large_2").state == "unavailable"
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("climate", "set_temperature", {
            "entity_id": "climate.sim_mill_large_2", "temperature": 20}, blocking=True)
    await hass.services.async_call(COTTAGE, "set_target", {
        "room": "large", "target": 15, "revision": 1}, blocking=True)
    await hass.async_block_till_done()
    status = hass.states.get("sensor.villa_kuppis_large_target")
    assert status.attributes["mode"] == "thermostat"
    assert hass.states.get("climate.sim_mill_large_1").attributes["temperature"] == 15.0
    await hass.services.async_call(SIM, "set_available", {
        "entity_id": ["sensor.sim_alpstuga_large_temperature", "climate.sim_mill_large_2"],
        "available": True}, blocking=True)
    await run(hass, freezer, 2)
    assert hass.states.get("sensor.villa_kuppis_large_target").attributes["mode"] == "follow"
    assert hass.states.get("climate.sim_mill_large_2").attributes["temperature"] == 18.0


async def test_hand_change_is_seen_as_manual(hass, freezer):
    await setup(hass)
    await run(hass, freezer, 2)
    await hass.services.async_call("climate", "set_temperature", {
        "entity_id": "climate.sim_mill_small_1", "temperature": 22}, blocking=True)
    await hass.async_block_till_done()
    status = hass.states.get("sensor.villa_kuppis_small_target")
    assert status.attributes["mode"] == "manual"
    assert status.attributes["manual_by"] == "climate.sim_mill_small_1"


async def test_state_survives_a_restart(hass, hass_storage):
    hass_storage[SIM] = {"version": 1, "minor_version": 1, "key": SIM, "data": {
        "saved_at": None, "rooms": {"small": {"temperature": 12.5, "heaters": {
            "climate.sim_mill_small_1": {"mode": "off", "setpoint": 9.0}}}}}}
    await setup(hass, cottage=False)
    assert hass.states.get("sensor.sim_alpstuga_small_temperature").state == "12.5"
    heater = hass.states.get("climate.sim_mill_small_1")
    assert heater.state == "off"
    assert heater.attributes["temperature"] == 9.0
