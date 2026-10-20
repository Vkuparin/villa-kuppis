"""Home pumps through Home Assistant (card H1.5).

The real PumpService runs on HAPumpClient against the fake Home Assistant
WebSocket server, which runs in its own thread like a separate process.
"""

from __future__ import annotations

import asyncio
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from villa_kuppis.ha_gateway import BindingStore, HomeAssistantGateway
from villa_kuppis.ha_pumps import client_factory, unit_from_state
from villa_kuppis.pump_service import PumpService, unit_state

from tests.test_ha_gateway import TOKEN, CLIMATE_FEATURES, FakeHA

UNIT = "0f1e2d3c-melcloud-unit"


def pump_attrs(**extra):
    attrs = {"friendly_name": "Olohuone", "temperature": 24.0, "current_temperature": 25.5,
             "min_temp": 10.0, "max_temp": 31.0, "target_temp_step": 0.5,
             "hvac_modes": ["off", "heat", "cool", "dry", "fan_only", "heat_cool"],
             "fan_modes": ["auto", "1", "2", "3", "4", "5"], "fan_mode": "auto",
             "supported_features": CLIMATE_FEATURES}
    attrs.update(extra)
    return attrs


def wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached in time")
        time.sleep(0.02)


class HAPumpServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.ha = FakeHA(lambda: datetime.now(timezone.utc))
        self.run_ha(self.ha.start())
        self.call_ha(lambda: self.ha.set_state("climate.olohuone", "cool", pump_attrs(), push=False))
        self.bindings = BindingStore(data / "sites.db")
        self.bindings.bind("home", UNIT, "climate.olohuone", "climate")
        self.gateway = HomeAssistantGateway("home", self.ha.url, lambda: TOKEN, self.bindings,
                                            command_timeout=2.0, confirm_timeout=2.0,
                                            retry_base=0.05, retry_max=0.2)
        self.service = PumpService(data, client=None, client_factory=client_factory(self.gateway),
                                   poll_interval=0.1, stale_after=5.0, readback_polls=10,
                                   readback_delay=0.1, command_timeout=3.0,
                                   automation_default_on=False)
        self.service.start()
        wait_until(lambda: self.service.store.unit_state(UNIT) is not None)

    def tearDown(self):
        self.service.close()
        self.run_ha(self.ha.stop())
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)
        self.loop.close()
        self.bindings.close()
        self.tmp.cleanup()

    def run_ha(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(10)

    def call_ha(self, fn):
        async def wrapper():
            return fn()
        return self.run_ha(wrapper())

    def finish(self, action, value):
        status, body = self.service.request(UNIT, action, value, "Ville")
        self.assertEqual(status, 202, body)
        wait_until(lambda: self.service.events(1)[0]["result"] != "pending")
        return self.service.events(1)[0]

    def test_state_reaches_the_pump_store(self):
        state = self.service.store.unit_state(UNIT)
        self.assertEqual((state["power"], state["mode"], state["setpoint"], state["room_temp"], state["fan"]),
                         (True, "Cool", 24.0, 25.5, "Auto"))
        self.assertEqual(state["caps"]["fan_speeds"], 5)
        self.assertTrue(state["caps"]["half_step"])
        self.assertTrue(state["caps"]["modes"]["Automatic"])

    def test_commands_are_confirmed_through_home_assistant(self):
        self.assertEqual(self.finish("setpoint", 22.5)["result"], "confirmed")
        self.assertEqual(self.finish("fan", "Three")["result"], "confirmed")
        self.assertEqual(self.finish("mode", "Dry")["result"], "confirmed")
        self.assertEqual(self.finish("power", False)["result"], "confirmed")
        services = [(s, d) for _, s, _, d in self.ha.calls]
        self.assertEqual(services, [("set_temperature", {"temperature": 22.5}),
                                    ("set_fan_mode", {"fan_mode": "3"}),
                                    ("set_hvac_mode", {"hvac_mode": "dry"}),
                                    ("turn_off", {})])

    def test_refused_command_fails(self):
        self.ha.fail["set_temperature"] = ("home_assistant_error", "MELCloud returned 500")
        self.ha.apply = False
        event = self.finish("setpoint", 21.0)
        self.assertEqual(event["result"], "failed")

    def test_disconnected_home_assistant_marks_the_service_stale(self):
        self.run_ha(self.ha.drop())
        self.ha.token = "revoked"
        wait_until(lambda: self.service.gateway.status()["error"] is not None)


class UnitShapeTests(unittest.TestCase):
    def test_unavailable_entity_reports_an_error_without_values(self):
        unit = unit_from_state({"device_id": "u", "label": "A", "kind": "climate",
                                "available": False, "status": "unavailable", "caps": {}})
        state = unit_state(unit, "2026-10-20T12:00:00+00:00")
        self.assertTrue(state["in_error"])
        self.assertIsNone(state["power"])
        self.assertIsNone(state["mode"])


if __name__ == "__main__":
    unittest.main()
