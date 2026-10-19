"""Tests for the pump service (card F2.3): state, commands, event log.

All tests run fully offline against the FakeClient built on the real
aiomelcloudhome models. Clocks are injected so freshness and staleness are
deterministic; timing values are small enough that the whole suite finishes
in seconds.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest import mock

from villa_kuppis.pump_probe import FakeClient, _unit_payload
from villa_kuppis.pump_service import PumpService, PumpStore, validate_request

SERVICE_KWARGS = dict(
    poll_interval=0.05,
    stale_after=0.5,
    readback_polls=3,
    readback_delay=0.02,
    command_timeout=2.0,
)


class FakeClock:
    def __init__(self):
        self._now = datetime(2026, 10, 19, 12, 0, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self._now

    def advance(self, seconds):
        self._now = self._now + timedelta(seconds=seconds)


class SlowFake(FakeClient):
    """A FakeClient whose control calls take a while (a slow cloud)."""

    def __init__(self, payloads, delay=0.5, **kwargs):
        super().__init__(payloads, **kwargs)
        self._delay = delay

    async def control_ata_unit(self, *args, **kwargs):
        await asyncio.sleep(self._delay)
        return await super().control_ata_unit(*args, **kwargs)


class FlakyFake(FakeClient):
    """A FakeClient whose context reads fail while `broken` is set."""

    def __init__(self, payloads):
        super().__init__(payloads)
        self.broken = False

    async def get_context(self):
        if self.broken:
            raise RuntimeError("no cloud")
        return await super().get_context()


def make_unit_payloads():
    return [
        _unit_payload("unit-1", "Makuuhuone", mode="Cool", power="True",
                      set_temp="23.5", room="26", fan="Auto"),
        _unit_payload("unit-2", "Keittiö", mode="Cool", power="False",
                      set_temp="22", room="27", fan="Two"),
        _unit_payload("unit-3", "Vessa", mode="Fan", power="True",
                      set_temp="23", room="25", fan="One"),
    ]


class ServiceTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="vk-pump-service-")
        self.tmp = self._tmpdir.name
        self._services = []

    def tearDown(self):
        for service in reversed(self._services):
            try:
                service.close()
            except Exception:
                pass
        self._tmpdir.cleanup()

    def wait_for(self, condition, timeout=5.0, message="condition not met"):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.01)
        self.fail(message)

    def _start(self, client, clock=None, data=None, **overrides):
        clock = clock or FakeClock()
        data = data or Path(self.tmp)
        kwargs: dict[str, Any] = dict(SERVICE_KWARGS)
        kwargs.update(overrides)
        service = PumpService(data, client=client, now=clock, **kwargs)
        service.start()
        self._services.append(service)
        return service, clock

    def _unit(self, service, unit_id):
        return next(u for u in service.view()["units"] if u["id"] == unit_id)

    def _latest_event(self, service):
        return service.events()[0]


class ViewTests(ServiceTestCase):
    def test_view_populates_units_and_state(self):
        client = FakeClient(make_unit_payloads())
        service, _ = self._start(client)
        self.wait_for(lambda: len(service.view()["units"]) == 3)
        view = service.view()
        self.assertEqual(view["service"]["state"], "ok")
        by_id = {u["id"]: u for u in view["units"]}
        unit = by_id["unit-1"]
        self.assertEqual(unit["name"], "Makuuhuone")
        self.assertIs(unit["power"], True)
        self.assertEqual(unit["mode"], "Cool")
        self.assertEqual(unit["setpoint"], 23.5)
        self.assertEqual(unit["fan"], "Auto")
        self.assertEqual(unit["room_temp"], 26.0)
        # The device reported "no error" (False), which is a value, not a gap.
        self.assertIs(unit["in_error"], False)
        self.assertIs(unit["fresh"], True)
        self.assertIsNone(unit["pending"])
        caps = unit["caps"]
        self.assertEqual(caps["min_cool"], 16)
        self.assertEqual(caps["max_cool"], 30)
        self.assertIs(caps["half_step"], True)
        self.assertEqual(caps["fan_speeds"], 5)
        self.assertEqual(by_id["unit-3"]["mode"], "Fan")

    def test_initial_error_state_when_cloud_unreachable(self):
        client = FakeClient(make_unit_payloads(),
                            context_error=RuntimeError("no cloud"))
        service, _ = self._start(client)
        self.wait_for(lambda: service.view()["service"]["state"] == "error")
        view = service.view()
        self.assertIn("cloud read failed", view["service"]["error"])
        self.assertEqual(view["units"], [])

    def test_caps_absent_when_not_reported(self):
        payload = _unit_payload("unit-x", "Ei capsia", caps={})
        client = FakeClient([payload])
        service, _ = self._start(client)
        self.wait_for(lambda: len(service.view()["units"]) == 1)
        unit = service.view()["units"][0]
        # No capabilities reported: caps is null, and commands that need
        # bounds are refused by validation.
        self.assertIsNone(unit["caps"])
        status, body = service.request("unit-x", "setpoint", 23.0, "Ville")
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "no_bounds")


class CommandTests(ServiceTestCase):
    def test_setpoint_confirmed(self):
        client = FakeClient(make_unit_payloads())
        service, _ = self._start(client)
        self.wait_for(lambda: len(service.view()["units"]) >= 1)
        status, _ = service.request("unit-1", "setpoint", 24.0, "Ville")
        self.assertEqual(status, 202)
        self.wait_for(lambda: self._latest_event(service)["result"] != "pending")
        unit = self._unit(service, "unit-1")
        self.assertEqual(unit["setpoint"], 24.0)
        self.assertIsNone(unit["pending"])
        event = self._latest_event(service)
        self.assertEqual(event["result"], "confirmed")
        self.assertEqual(event["actor"], "Ville")
        self.assertEqual(event["action"], "setpoint")
        self.assertEqual(event["requested"], 24.0)
        self.assertEqual(len(client.control_calls), 1)
        self.assertEqual(client.control_calls[0][1]["set_temperature"], 24.0)

    def test_pending_shown_while_confirming(self):
        client = SlowFake(make_unit_payloads(), delay=0.4)
        service, _ = self._start(client)
        self.wait_for(lambda: len(service.view()["units"]) >= 1)
        status, _ = service.request("unit-1", "setpoint", 25.0, "Ville")
        self.assertEqual(status, 202)
        unit = self._unit(service, "unit-1")
        self.assertIsNotNone(unit["pending"])
        self.assertEqual(unit["pending"]["action"], "setpoint")
        self.assertEqual(unit["pending"]["requested"], 25.0)
        self.assertEqual(unit["pending"]["actor"], "Ville")
        # The device-reported state still shows the old value.
        self.assertEqual(unit["setpoint"], 23.5)
        self.wait_for(lambda: self._latest_event(service)["result"] == "confirmed")
        self.assertEqual(self._unit(service, "unit-1")["setpoint"], 25.0)

    def test_setpoint_failed_when_cloud_keeps_old_value(self):
        def revert(payload):
            settings = {s["name"]: s["value"] for s in payload["settings"]}
            settings["SetTemperature"] = "23.5"
            payload["settings"] = [
                {"name": n, "value": v} for n, v in settings.items()
            ]

        client = FakeClient(make_unit_payloads(), mutates=[revert])
        service, _ = self._start(client)
        self.wait_for(lambda: len(service.view()["units"]) >= 1)
        status, _ = service.request("unit-1", "setpoint", 24.0, "Ville")
        self.assertEqual(status, 202)
        self.wait_for(lambda: self._latest_event(service)["result"] == "failed")
        self.assertEqual(self._unit(service, "unit-1")["setpoint"], 23.5)
        self.assertIn("not confirmed", self._latest_event(service)["detail"])

    def test_write_response_failure_but_change_confirmed(self):
        client = FakeClient(make_unit_payloads(),
                            put_errors=[RuntimeError("boom")])
        service, _ = self._start(client)
        self.wait_for(lambda: len(service.view()["units"]) >= 1)
        status, _ = service.request("unit-1", "power", False, "Ville")
        self.assertEqual(status, 202)
        self.wait_for(lambda: self._latest_event(service)["result"] == "confirmed")
        self.assertIn("write response failed",
                      self._latest_event(service)["detail"])
        self.assertIs(self._unit(service, "unit-1")["power"], False)

    def test_failed_when_readback_fails(self):
        # The first two context reads succeed (poll + state read); from the
        # third on the cloud is unreachable, so no read-back can confirm.
        client = FakeClient(make_unit_payloads(),
                            context_error=RuntimeError("no cloud"),
                            context_error_after=2)
        service, _ = self._start(client, poll_interval=5.0)
        self.wait_for(lambda: len(service.view()["units"]) >= 1)
        # The first two polls have happened (poll_interval=5 keeps the poll
        # loop out of the way during the command).
        status, _ = service.request("unit-1", "setpoint", 24.0, "Ville")
        self.assertEqual(status, 202)
        self.wait_for(lambda: self._latest_event(service)["result"] == "failed")
        self.assertIn("read-back", self._latest_event(service)["detail"])
        self.assertEqual(self._unit(service, "unit-1")["setpoint"], 23.5)

    def test_power_mode_fan_confirmed(self):
        client = FakeClient(make_unit_payloads())
        service, _ = self._start(client)
        self.wait_for(lambda: len(service.view()["units"]) >= 3)

        status, _ = service.request("unit-2", "power", True, "Ville")
        self.assertEqual(status, 202)
        self.wait_for(lambda: self._latest_event(service)["result"] == "confirmed")
        self.assertIs(self._unit(service, "unit-2")["power"], True)

        status, _ = service.request("unit-2", "mode", "Heat", "Ville")
        self.assertEqual(status, 202)
        self.wait_for(
            lambda: len(service.events()) >= 2
            and service.events()[0]["result"] == "confirmed"
        )
        self.assertEqual(self._unit(service, "unit-2")["mode"], "Heat")

        status, _ = service.request("unit-2", "fan", "Three", "Ville")
        self.assertEqual(status, 202)
        self.wait_for(
            lambda: len(service.events()) >= 3
            and service.events()[0]["result"] == "confirmed"
        )
        self.assertEqual(self._unit(service, "unit-2")["fan"], "Three")

    def test_command_error_does_not_kill_worker(self):
        # A command that blows up mid-pipeline must fail only that
        # command: the worker must keep processing the next one.
        client = FakeClient(make_unit_payloads())
        service, _ = self._start(client)
        self.wait_for(lambda: len(service.view()["units"]) >= 1)
        real = validate_request
        calls = {"n": 0}

        def flaky(state, action, value):
            calls["n"] += 1
            if calls["n"] == 2:  # the worker's own validation pass
                raise RuntimeError("boom in validation")
            return real(state, action, value)

        with mock.patch("villa_kuppis.pump_service.validate_request",
                        side_effect=flaky):
            status, _ = service.request("unit-1", "setpoint", 24.0, "Ville")
            self.assertEqual(status, 202)
            self.wait_for(
                lambda: service.events()
                and service.events()[0]["result"] != "pending"
            )
            self.assertEqual(service.events()[0]["result"], "failed")
            self.assertIn("command failed", service.events()[0]["detail"])
            # The worker survived: the next command still completes.
            status, _ = service.request("unit-1", "setpoint", 25.0, "Ville")
            self.assertEqual(status, 202)
            self.wait_for(
                lambda: len(service.events()) >= 2
                and service.events()[0]["result"] == "confirmed"
            )
        self.assertEqual(len(client.control_calls), 1)
        self.assertEqual(client.control_calls[0][1]["set_temperature"], 25.0)
        self.assertEqual(self._unit(service, "unit-1")["setpoint"], 25.0)


class StaleTests(ServiceTestCase):
    def test_stale_when_cloud_polls_fail(self):
        client = FakeClient(make_unit_payloads(),
                            context_error=RuntimeError("no cloud"),
                            context_error_after=2)
        service, clock = self._start(client)
        self.wait_for(lambda: service.view()["service"]["state"] == "ok")
        self.wait_for(lambda: all(u["fresh"] for u in service.view()["units"]))
        # Wait until the cloud failure has actually landed: the fake allows
        # exactly two reads, so from this point on every poll fails.
        self.wait_for(lambda: service.view()["service"]["error"] is not None)
        # Now advance the clock past the freshness window.
        clock.advance(1.0)
        self.wait_for(lambda: service.view()["service"]["state"] == "stale")
        view = service.view()
        self.assertIn("cloud read failed", view["service"]["error"])
        for unit in view["units"]:
            self.assertIs(unit["fresh"], False)
        # The last reported values are still shown: a gap is a gap, not a reset.
        self.assertEqual(self._unit(service, "unit-1")["setpoint"], 23.5)

    def test_recovers_after_cloud_returns(self):
        client = FlakyFake(make_unit_payloads())
        service, clock = self._start(client)
        self.wait_for(lambda: service.view()["service"]["state"] == "ok")
        # The cloud goes down and the freshness window elapses.
        client.broken = True
        clock.advance(1.0)
        self.wait_for(lambda: service.view()["service"]["state"] == "stale")
        # The cloud comes back: the next poll makes everything fresh again.
        client.broken = False
        self.wait_for(lambda: service.view()["service"]["state"] == "ok")
        self.wait_for(lambda: all(u["fresh"] for u in service.view()["units"]))
        self.assertEqual(self._unit(service, "unit-1")["setpoint"], 23.5)


class RequestTests(ServiceTestCase):
    def test_unknown_unit_404(self):
        client = FakeClient(make_unit_payloads())
        service, _ = self._start(client)
        self.wait_for(lambda: len(service.view()["units"]) >= 1)
        status, body = service.request("nope", "power", True, "Ville")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"], "unknown_unit")

    def test_unknown_action_400(self):
        client = FakeClient(make_unit_payloads())
        service, _ = self._start(client)
        self.wait_for(lambda: len(service.view()["units"]) >= 1)
        status, body = service.request("unit-1", "vane", 1, "Ville")
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "unknown_action")

    def test_validation_error_400(self):
        client = FakeClient(make_unit_payloads())
        service, _ = self._start(client)
        self.wait_for(lambda: len(service.view()["units"]) >= 3)
        status, body = service.request("unit-1", "setpoint", 200.0, "Ville")
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "out_of_bounds")
        status, body = service.request("unit-3", "setpoint", 23.0, "Ville")
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "fan_mode")
        status, body = service.request("unit-1", "mode", "Cooling", "Ville")
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "bad_mode")
        status, body = service.request("unit-1", "power", "on", "Ville")
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "bad_power")
        self.assertEqual(len(client.control_calls), 0)

    def test_pending_conflict_409(self):
        client = SlowFake(make_unit_payloads(), delay=0.4)
        service, _ = self._start(client)
        self.wait_for(lambda: len(service.view()["units"]) >= 1)
        status, _ = service.request("unit-1", "setpoint", 24.0, "Ville")
        self.assertEqual(status, 202)
        self.wait_for(lambda: self._unit(service, "unit-1")["pending"] is not None)
        status, body = service.request("unit-1", "setpoint", 25.0, "Ville")
        self.assertEqual(status, 409)
        self.assertEqual(body["error"], "pending")
        self.wait_for(lambda: self._latest_event(service)["result"] == "confirmed")
        # After the first command is done, the next one succeeds.
        status, _ = service.request("unit-1", "setpoint", 25.0, "Ville")
        self.assertEqual(status, 202)
        self.wait_for(lambda: service.events()[0]["result"] == "confirmed")
        self.assertEqual(self._unit(service, "unit-1")["setpoint"], 25.0)

    def test_concurrent_same_unit_one_409(self):
        # Two requests for the same unit racing: exactly one wins the
        # pending slot (202); the loser gets 409, not a 503, and only the
        # winner ever touches the device.
        client = SlowFake(make_unit_payloads(), delay=0.4)
        service, _ = self._start(client)
        self.wait_for(lambda: len(service.view()["units"]) >= 1)
        barrier = threading.Barrier(2)
        results: list[tuple[int, dict]] = []
        results_lock = threading.Lock()

        def post():
            barrier.wait()
            status, body = service.request("unit-1", "setpoint", 24.0, "Ville")
            with results_lock:
                results.append((status, body))

        threads = [threading.Thread(target=post) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        for t in threads:
            self.assertFalse(t.is_alive())
        self.assertEqual(sorted(s for s, _ in results), [202, 409])
        body409 = next(b for s, b in results if s == 409)
        self.assertEqual(body409["error"], "pending")
        self.wait_for(
            lambda: all(e["result"] != "pending" for e in service.events())
            and any(e["result"] == "confirmed" for e in service.events())
        )
        confirmed = [e for e in service.events() if e["result"] == "confirmed"]
        failed = [e for e in service.events() if e["result"] == "failed"]
        self.assertEqual(len(confirmed), 1)
        # The loser either left no trace (caught by the pending check) or a
        # failed event (lost the reservation race); never a second confirmed
        # command and never a pending one.
        self.assertLessEqual(len(failed), 1)
        for e in failed:
            self.assertIn("another change", e["detail"])
        self.assertEqual(len(client.control_calls), 1)
        self.assertEqual(client.control_calls[0][1]["set_temperature"], 24.0)
        self.assertEqual(self._unit(service, "unit-1")["setpoint"], 24.0)

    def test_submit_rejection_reason_not_recheck(self):
        # Re-verification 002: the response must follow the reason
        # submit() decided under the reservation lock, not a later
        # has_pending() guess that the slot may have cleared in the
        # meantime. The busy arm reproduces that interleaving directly:
        # a "pending" refusal that survives the slot clearing must read
        # as 409, never as a shutdown.
        client = FakeClient(make_unit_payloads())
        service, _ = self._start(client)
        self.wait_for(lambda: len(service.view()["units"]) >= 1)
        gw = service.gateway
        real_submit = gw.submit

        def busy_submit(*args, **kwargs):
            return "pending"
        gw.submit = busy_submit
        try:
            status, body = service.request("unit-1", "setpoint", 24.0, "Ville")
        finally:
            gw.submit = real_submit
        self.assertEqual(status, 409)
        self.assertEqual(body["error"], "pending")
        self.assertEqual(service.events()[0]["result"], "failed")
        self.assertIn("another change", service.events()[0]["detail"])

        def stopped_submit(*args, **kwargs):
            return "stopped"
        gw.submit = stopped_submit
        try:
            status, body = service.request("unit-1", "setpoint", 25.0, "Ville")
        finally:
            gw.submit = real_submit
        self.assertEqual(status, 503)
        self.assertEqual(body["error"], "not_running")
        failed = [e for e in service.events() if e["result"] == "failed"]
        self.assertEqual(len(failed), 2)

    def test_actor_truncated_and_default(self):
        client = FakeClient(make_unit_payloads())
        service, _ = self._start(client)
        self.wait_for(lambda: len(service.view()["units"]) >= 1)
        status, _ = service.request("unit-1", "power", False, "x" * 200)
        self.assertEqual(status, 202)
        self.wait_for(lambda: self._latest_event(service)["result"] != "pending")
        self.assertEqual(self._latest_event(service)["actor"], "x" * 80)
        status, _ = service.request("unit-1", "power", True, None)
        self.assertEqual(status, 202)
        self.wait_for(
            lambda: len(service.events()) >= 2
            and service.events()[0]["result"] != "pending"
        )
        self.assertEqual(service.events()[0]["actor"], "web")


class OrphanTests(ServiceTestCase):
    def test_pending_orphaned_on_restart(self):
        client = SlowFake(make_unit_payloads(), delay=30.0)
        clock = FakeClock()
        data = Path(self.tmp) / "orphan"
        kw: dict[str, Any] = dict(SERVICE_KWARGS)
        service = PumpService(data, client=client, now=clock, **kw)
        service.start()
        self.wait_for(lambda: len(service.view()["units"]) >= 1)
        status, _ = service.request("unit-1", "setpoint", 24.0, "Ville")
        self.assertEqual(status, 202)
        self.wait_for(
            lambda: next(u for u in service.view()["units"]
                         if u["id"] == "unit-1")["pending"] is not None
        )
        service.close()  # the command is still in flight: it becomes an orphan
        # A restart seconds later must already fail the event: the pending
        # queue never survives a process restart, so no age window applies.
        clock.advance(5)
        service2 = PumpService(data, client=client, now=clock, **kw)
        service2.start()
        self._services.append(service2)
        event = service2.events()[0]
        self.assertEqual(event["result"], "failed")
        self.assertIn("restarted before the change was confirmed",
                      event["detail"])


class ValidationTests(ServiceTestCase):
    def _state(self, **overrides):
        state = {
            "id": "u", "name": "U", "power": True, "mode": "Cool",
            "setpoint": 23.5, "fan": "Auto", "room_temp": 26.0,
            "in_error": None,
            "caps": {
                "min_heat": 10, "max_heat": 30,
                "min_cool": 16, "max_cool": 30,
                "min_auto": 10, "max_auto": 30,
                "half_step": True, "fan_speeds": 5, "auto_fan": None,
                "modes": {"Cool": None, "Heat": None, "Dry": None,
                          "Fan": None, "Automatic": None},
            },
            "updated_at": "2026-10-19T12:00:00+00:00",
        }
        state.update(overrides)
        return state

    def test_setpoint_bounds(self):
        state = self._state()
        ok, _, code, _ = validate_request(state, "setpoint", 15.0)
        self.assertFalse(ok)
        self.assertEqual(code, "out_of_bounds")
        ok, value, _, _ = validate_request(state, "setpoint", 30.0)
        self.assertTrue(ok)
        self.assertEqual(value, 30.0)
        ok, _, code, _ = validate_request(state, "setpoint", 30.5)
        self.assertFalse(ok)
        self.assertEqual(code, "out_of_bounds")

    def test_setpoint_step(self):
        ok, _, code, _ = validate_request(self._state(), "setpoint", 23.25)
        self.assertFalse(ok)
        self.assertEqual(code, "step")
        ok, _, _, _ = validate_request(self._state(), "setpoint", 23.5)
        self.assertTrue(ok)
        caps = dict(self._state()["caps"], half_step=False)
        ok, _, code, _ = validate_request(self._state(caps=caps), "setpoint", 23.5)
        self.assertFalse(ok)
        self.assertEqual(code, "step")
        ok, _, _, _ = validate_request(self._state(caps=caps), "setpoint", 24.0)
        self.assertTrue(ok)

    def test_setpoint_mode_cases(self):
        ok, _, code, _ = validate_request(
            self._state(mode="Fan"), "setpoint", 23.0)
        self.assertFalse(ok)
        self.assertEqual(code, "fan_mode")
        ok, _, code, _ = validate_request(
            self._state(mode=None), "setpoint", 23.0)
        self.assertFalse(ok)
        self.assertEqual(code, "no_mode")
        ok, _, code, _ = validate_request(
            self._state(caps=None), "setpoint", 23.0)
        self.assertFalse(ok)
        self.assertEqual(code, "no_bounds")
        ok, _, code, _ = validate_request(
            self._state(caps={}), "setpoint", 23.0)
        self.assertFalse(ok)
        self.assertEqual(code, "no_bounds")

    def test_mode_validation(self):
        ok, _, _, _ = validate_request(self._state(), "mode", "Heat")
        self.assertTrue(ok)
        ok, _, code, _ = validate_request(self._state(), "mode", "Cooling")
        self.assertFalse(ok)
        self.assertEqual(code, "bad_mode")
        caps = dict(self._state()["caps"])
        caps["modes"] = {"Cool": None, "Heat": False, "Dry": None,
                         "Fan": None, "Automatic": None}
        ok, _, code, _ = validate_request(self._state(caps=caps), "mode", "Heat")
        self.assertFalse(ok)
        self.assertEqual(code, "bad_mode")

    def test_fan_validation(self):
        ok, _, _, _ = validate_request(self._state(), "fan", "Auto")
        self.assertTrue(ok)
        ok, _, code, _ = validate_request(self._state(), "fan", "Six")
        self.assertFalse(ok)
        self.assertEqual(code, "bad_fan")
        caps = dict(self._state()["caps"], fan_speeds=2)
        ok, _, code, _ = validate_request(self._state(caps=caps), "fan", "Three")
        self.assertFalse(ok)
        self.assertEqual(code, "bad_fan")
        ok, _, _, _ = validate_request(self._state(caps=caps), "fan", "Two")
        self.assertTrue(ok)
        caps = dict(self._state()["caps"], auto_fan=False)
        ok, _, code, _ = validate_request(self._state(caps=caps), "fan", "Auto")
        self.assertFalse(ok)
        self.assertEqual(code, "bad_fan")

    def test_power_and_not_number(self):
        ok, value, _, _ = validate_request(self._state(), "power", True)
        self.assertTrue(ok)
        self.assertIs(value, True)
        ok, _, code, _ = validate_request(self._state(), "power", "on")
        self.assertFalse(ok)
        self.assertEqual(code, "bad_power")
        ok, _, code, _ = validate_request(self._state(), "setpoint", "24")
        self.assertFalse(ok)
        self.assertEqual(code, "not_number")
        ok, _, code, _ = validate_request(self._state(), "setpoint", True)
        self.assertFalse(ok)
        self.assertEqual(code, "not_number")
        ok, _, code, _ = validate_request(self._state(), "setpoint", float("inf"))
        self.assertFalse(ok)
        self.assertEqual(code, "not_number")


class StoreTests(unittest.TestCase):
    def test_store_roundtrip_and_events(self):
        tmp = Path(tempfile.mkdtemp(prefix="vk-pump-store-"))
        store = PumpStore(tmp / "pumps.db")
        try:
            store.upsert_units([
                {"id": "u1", "name": "A", "power": True, "mode": "Cool",
                 "setpoint": 23.5, "fan": "Auto", "room_temp": 26.0,
                 "in_error": None, "caps": {"min_cool": 16},
                 "updated_at": "2026-10-19T12:00:00+00:00"},
            ])
            state = store.unit_state("u1")
            self.assertIsNotNone(state)
            self.assertEqual(state["name"], "A")
            self.assertEqual(state["caps"], {"min_cool": 16})
            self.assertIsNone(store.unit_state("nope"))
            updated = dict(state, name="B",
                           updated_at="2026-10-19T12:01:00+00:00")
            store.upsert_units([updated])
            after = store.unit_state("u1")
            self.assertIsNotNone(after)
            self.assertEqual(after["name"], "B")
            event_id = store.add_event(
                "2026-10-19T12:02:00+00:00", "u1", "B", "Ville",
                "setpoint", 24.0,
            )
            event = store.events()[0]
            self.assertEqual(event["id"], event_id)
            self.assertEqual(event["result"], "pending")
            self.assertEqual(event["requested"], 24.0)
            store.finish_event(event_id, "confirmed", "")
            self.assertEqual(store.events()[0]["result"], "confirmed")
            # A pending event is marked failed by the orphan sweep,
            # regardless of age: the queue never survives a restart.
            orphan_id = store.add_event(
                "2026-10-19T10:00:00+00:00", "u1", "B", "Ville",
                "power", False)
            marked = store.fail_orphaned_pending()
            self.assertEqual(marked, 1)
            by_id = {e["id"]: e for e in store.events()}
            self.assertEqual(by_id[event_id]["result"], "confirmed")
            self.assertEqual(by_id[orphan_id]["result"], "failed")
            self.assertIn("restarted before the change was confirmed",
                          by_id[orphan_id]["detail"])
        finally:
            store.close()
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
