"""The cottage integration inside a real Home Assistant core (card H2.2).

Heaters are plain climate states and the climate services are mocked, so
each test sees exactly which commands the controller sent.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    async_mock_service,
)

DOMAIN = "villa_kuppis_cottage"
CONFIG = {DOMAIN: {
    "safe_setpoint": 7,
    "rooms": {
        "large": {"name": "Iso huone", "sensor": "sensor.alpstuga_large",
                  "heaters": ["climate.mill_1", "climate.mill_2"]},
        "small": {"name": "Pieni huone", "sensor": "sensor.alpstuga_small",
                  "heaters": ["climate.mill_4"]},
    },
}}
HEATER_ATTRS = {"temperature": 7.0, "min_temp": 5.0, "max_temp": 35.0,
                "target_temp_step": 0.5, "hvac_modes": ["off", "heat"]}


def set_heater(hass, entity_id, setpoint, mode="heat"):
    hass.states.async_set(entity_id, mode, {**HEATER_ATTRS, "temperature": setpoint})


@pytest.fixture
async def calls(hass: HomeAssistant):
    set_heater(hass, "climate.mill_1", 7.0)
    set_heater(hass, "climate.mill_2", 7.0)
    set_heater(hass, "climate.mill_4", 7.0)
    hass.states.async_set("sensor.alpstuga_large", "6.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("sensor.alpstuga_small", "6.5", {"unit_of_measurement": "°C"})
    return async_mock_service(hass, "climate", "set_temperature")


async def setup(hass):
    assert await async_setup_component(hass, DOMAIN, CONFIG)
    await hass.async_block_till_done()


def sent(calls):
    return [(c.data["entity_id"], c.data["temperature"]) for c in calls]


def status(hass, room="large"):
    return hass.states.get(f"sensor.villa_kuppis_{room}_target")


async def set_target(hass, room, target, revision, minutes=45):
    await hass.services.async_call(DOMAIN, "set_target", {
        "room": room, "target": target, "revision": revision, "valid_minutes": minutes,
    }, blocking=True)
    await hass.async_block_till_done()


async def test_without_a_target_the_heaters_hold_the_safe_setpoint(hass, calls):
    set_heater(hass, "climate.mill_1", 15.0)
    await setup(hass)
    assert sent(calls) == [("climate.mill_1", 7.0)]
    state = status(hass)
    assert state.state == "7.0"
    assert state.attributes["mode"] == "fallback"
    assert state.attributes["revision"] is None


async def test_target_is_followed_with_the_room_sensor(hass, calls):
    await setup(hass)
    await set_target(hass, "large", 18, revision=100)
    assert sent(calls) == [("climate.mill_1", 21.0), ("climate.mill_2", 21.0)]
    state = status(hass)
    assert state.state == "18.0"
    assert state.attributes["mode"] == "follow"
    assert state.attributes["drive"] == "high"
    assert state.attributes["revision"] == 100
    assert state.attributes["requested_target"] == 18.0
    assert status(hass, "small").attributes["mode"] == "fallback"


async def test_older_revision_is_refused(hass, calls):
    await setup(hass)
    await set_target(hass, "large", 18, revision=100)
    with pytest.raises(ServiceValidationError):
        await set_target(hass, "large", 10, revision=99)
    assert status(hass).attributes["requested_target"] == 18.0
    # The same revision again is a heartbeat.
    await set_target(hass, "large", 18, revision=100)


async def test_unknown_room_is_refused(hass, calls):
    await setup(hass)
    with pytest.raises(ServiceValidationError):
        await set_target(hass, "sauna", 18, revision=1)


async def test_expired_target_falls_back(hass, calls, freezer: FrozenDateTimeFactory):
    await setup(hass)
    await set_target(hass, "large", 18, revision=1, minutes=30)
    calls.clear()
    for heater in ("climate.mill_1", "climate.mill_2"):
        set_heater(hass, heater, 21.0)
    await hass.async_block_till_done()
    freezer.tick(timedelta(minutes=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert status(hass).attributes["mode"] == "fallback"
    assert status(hass).attributes["reason"] == "the target expired"
    assert sent(calls) == [("climate.mill_1", 7.0), ("climate.mill_2", 7.0)]


async def test_stale_sensor_uses_the_heater_thermostats(hass, calls, freezer):
    await setup(hass)
    await set_target(hass, "large", 16, revision=1, minutes=600)
    for heater in ("climate.mill_1", "climate.mill_2"):
        set_heater(hass, heater, 19.0)
    calls.clear()
    freezer.tick(timedelta(minutes=31))   # no sensor report for 31 minutes
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert status(hass).attributes["mode"] == "thermostat"
    assert sent(calls) == [("climate.mill_1", 16.0), ("climate.mill_2", 16.0)]


async def test_schedule_runs_through_an_outage_and_is_stored(
        hass, calls, freezer: FrozenDateTimeFactory, hass_storage):
    await setup(hass)
    now = dt_util.utcnow()
    await hass.services.async_call(DOMAIN, "set_target", {
        "room": "large", "target": 8, "revision": 5, "valid_minutes": 45,
        "schedule": [{"start": (now + timedelta(minutes=60)).isoformat(), "target": 16},
                     {"start": (now + timedelta(minutes=120)).isoformat(), "target": 10}],
    }, blocking=True)
    await hass.async_block_till_done()
    state = status(hass)
    assert state.attributes["schedule_steps"] == 2
    assert state.attributes["valid_until"] == (now + timedelta(minutes=135)).isoformat()
    assert state.state == "8.0"
    saved = hass_storage[DOMAIN]["data"]["targets"]["large"]["schedule"]
    assert [value for _start, value in saved] == [16, 10]
    for minutes, expected in ((61, "16.0"), (121, "10.0")):
        freezer.move_to(now + timedelta(minutes=minutes))
        hass.states.async_set("sensor.alpstuga_large", str(minutes / 10),
                              {"unit_of_measurement": "°C"})
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        assert status(hass).state == expected
        assert status(hass).attributes["mode"] == "follow"
    freezer.move_to(now + timedelta(minutes=136))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert status(hass).attributes["mode"] == "fallback"


async def test_target_survives_a_restart(hass, calls, hass_storage):
    valid_until = (dt_util.utcnow() + timedelta(minutes=20)).isoformat()
    hass_storage[DOMAIN] = {"version": 1, "minor_version": 1, "key": DOMAIN, "data": {
        "targets": {"large": {"value": 12.0, "revision": 7, "valid_until": valid_until}}}}
    await setup(hass)
    state = status(hass)
    assert state.attributes["mode"] == "follow"
    assert state.attributes["revision"] == 7


async def test_hand_change_pauses_the_room_until_cleared(hass, calls, freezer):
    await setup(hass)
    await set_target(hass, "large", 18, revision=1)
    set_heater(hass, "climate.mill_1", 21.0)
    set_heater(hass, "climate.mill_2", 21.0)
    await hass.async_block_till_done()
    calls.clear()
    set_heater(hass, "climate.mill_1", 24.0)   # someone in Google Home
    await hass.async_block_till_done()
    assert status(hass).attributes["mode"] == "manual"
    assert status(hass).attributes["manual_by"] == "climate.mill_1"
    assert status(hass).state == "unknown"
    hass.states.async_set("sensor.alpstuga_large", "19.5")
    await hass.async_block_till_done()
    assert calls == []
    freezer.tick(timedelta(minutes=2))   # past the per-heater command interval
    await hass.services.async_call(DOMAIN, "clear_manual", {"room": "large"}, blocking=True)
    await hass.async_block_till_done()
    assert status(hass).attributes["mode"] == "follow"
    assert ("climate.mill_1", 5.0) in sent(calls)


async def test_unavailable_heater_is_skipped(hass, calls):
    hass.states.async_set("climate.mill_2", "unavailable", {})
    await setup(hass)
    await set_target(hass, "large", 18, revision=1)
    assert sent(calls) == [("climate.mill_1", 21.0)]
