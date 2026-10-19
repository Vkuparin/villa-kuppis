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
from villa_kuppis.pump_service import (
    PumpService, PumpStore, make_live_service, validate_request,
)

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


class LiveStartupTests(ServiceTestCase):
    credentials = {
        "VILLA_KUPPIS_MELCLOUD_USERNAME": "offline@example.invalid",
        "VILLA_KUPPIS_MELCLOUD_PASSWORD": "offline-test-password",
    }

    def test_live_session_created_used_and_closed_on_gateway_loop(self):
        from aiohttp import ClientSession

        sessions = []
        create_loops = []
        read_loops = []
        close_loops = []
        original_close = ClientSession.close
        fake = FakeClient(make_unit_payloads())

        def create_session():
            create_loops.append(asyncio.get_running_loop())
            session = ClientSession()
            sessions.append(session)
            return session

        async def read_context(client):
            read_loops.append(asyncio.get_running_loop())
            return await fake.get_context()

        async def close_session(session):
            close_loops.append(asyncio.get_running_loop())
            await original_close(session)

        with mock.patch.dict("os.environ", self.credentials, clear=True), \
                mock.patch("aiohttp.ClientSession", side_effect=create_session) as factory, \
                mock.patch.object(ClientSession, "close", new=close_session), \
                mock.patch("aiomelcloudhome.MELCloudHome.get_context", new=read_context):
            # Construct from synchronous startup, before any event loop exists.
            service = make_live_service(self.tmp, **SERVICE_KWARGS)
            self._services.append(service)
            factory.assert_not_called()
            service.start()
            self.wait_for(lambda: len(read_loops) >= 2)
            self.assertEqual(len(service.view()["units"]), 3)
            gateway_loop = service.gateway._loop
            service.close()
            factory.assert_called_once_with()

        self.assertEqual(create_loops, [gateway_loop])
        self.assertTrue(all(loop is gateway_loop for loop in read_loops))
        self.assertEqual(close_loops, [gateway_loop])
        self.assertTrue(sessions[0].closed)
        self.assertTrue(gateway_loop.is_closed())

    def test_live_factory_failure_reports_status_and_stops_cleanly(self):
        with mock.patch.dict("os.environ", self.credentials, clear=True), \
                mock.patch("villa_kuppis.pump_service._default_client_factory",
                           side_effect=RuntimeError("offline startup failure")) as factory:
            service = make_live_service(self.tmp, **SERVICE_KWARGS)
            self._services.append(service)
            service.start()
            self.wait_for(lambda: service.view()["service"]["state"] == "error")
            self.assertIn("cloud startup failed", service.view()["service"]["error"])
            self.assertFalse(service.gateway.running)
            self.assertEqual(service.request("unit-1", "power", True, "test")[0], 503)
            thread = service.gateway._thread
            service.close()
            factory.assert_called_once()
        self.assertFalse(thread.is_alive())
        self.assertTrue(service.gateway._loop.is_closed())


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

    def test_confirmed_readback_records_history_before_next_poll(self):
        service, clock = self._start(FakeClient(make_unit_payloads()),
                                     poll_interval=60)
        self.wait_for(lambda: len(service.view()["units"]) >= 1)
        status, _ = service.request("unit-1", "setpoint", 24.0, "Ville")
        self.assertEqual(status, 202)
        self.wait_for(lambda: self._latest_event(service)["result"] == "confirmed")
        rows = service.store.pump_history(
            "unit-1", clock() - timedelta(seconds=1),
            clock() + timedelta(seconds=1), 288)
        self.assertEqual(rows[-1][2], 24.0)

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


class StoreHistoryTests(unittest.TestCase):
    """Store-level history, outdoor and price queries (card F3.3)."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="vk-pump-store-")
        self.tmp = self._tmpdir.name
        self.store = PumpStore(Path(self.tmp) / "pumps.db")

    def tearDown(self):
        self.store.close()
        self._tmpdir.cleanup()

    @staticmethod
    def _state(unit_id, iso, power, mode, setpoint, fan, room):
        return {"id": unit_id, "name": "X", "power": power, "mode": mode,
                "setpoint": setpoint, "fan": fan, "room_temp": room,
                "in_error": False, "caps": None, "updated_at": iso}

    def test_record_history_first_row(self):
        s = self._state("u1", "2026-10-19T12:00:00+00:00", True, "Cool",
                        23.0, "Auto", 26.0)
        self.assertEqual(self.store.record_history([s]), 1)

    def test_history_batch_failure_rolls_back_before_later_writes(self):
        base = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
        good = self._state("u1", base.isoformat(), True, "Cool",
                           24.0, "Auto", 25.0)
        bad = dict(good, id=None)  # violates the history NOT NULL constraint
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.record_history([good, bad])
        # An unrelated later commit must not publish a partial history batch.
        self.store.outdoor_upsert("home", [(base, 10)])
        self.assertEqual(self.store.pump_history(
            "u1", base - timedelta(seconds=1),
            base + timedelta(seconds=1), 10), [])

    def test_record_history_skips_unchanged_within_interval(self):
        a = self._state("u1", "2026-10-19T12:00:00+00:00", True, "Cool",
                        23.0, "Auto", 26.0)
        b = self._state("u1", "2026-10-19T12:02:00+00:00", True, "Cool",
                        23.0, "Auto", 26.0)  # 2 min later, identical
        self.assertEqual(self.store.record_history([a]), 1)
        self.assertEqual(self.store.record_history([b]), 0)

    def test_record_history_writes_on_change(self):
        a = self._state("u1", "2026-10-19T12:00:00+00:00", True, "Cool",
                        23.0, "Auto", 26.0)
        b = self._state("u1", "2026-10-19T12:02:00+00:00", True, "Cool",
                        24.0, "Auto", 26.0)  # setpoint changed
        self.assertEqual(self.store.record_history([a]), 1)
        self.assertEqual(self.store.record_history([b]), 1)

    def test_record_history_writes_on_interval_expiry(self):
        a = self._state("u1", "2026-10-19T12:00:00+00:00", True, "Cool",
                        23.0, "Auto", 26.0)
        b = self._state("u1", "2026-10-19T12:06:00+00:00", True, "Cool",
                        23.0, "Auto", 26.0)  # 6 min later, identical
        self.assertEqual(self.store.record_history([a]), 1)
        self.assertEqual(self.store.record_history([b]), 1)

    def test_pump_history_bucketing_and_order(self):
        base = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
        states = []
        for i in range(30):
            iso = (base - timedelta(hours=23) + timedelta(minutes=48 * i)
                   ).isoformat()
            states.append(self._state("u1", iso, True, "Cool",
                                      20.0 + i * 0.1, "Auto",
                                      20.0 + i * 0.2))
        self.store.record_history(states)
        rows = self.store.pump_history("u1", base - timedelta(hours=24),
                                       base, 288)
        self.assertLessEqual(len(rows), 288)
        self.assertGreater(len(rows), 0)
        ts = [r[0] for r in rows]
        self.assertEqual(ts, sorted(ts))

    def test_pump_history_respects_max_points_cap(self):
        base = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
        states = []
        for i in range(100):
            iso = (base - timedelta(minutes=30 * i)).isoformat()
            states.append(self._state("u1", iso, True, "Cool", 20.0, "Auto",
                                      20.0 + i * 0.01))
        self.store.record_history(states)
        rows = self.store.pump_history("u1", base - timedelta(hours=10),
                                       base, 50)
        self.assertLessEqual(len(rows), 50)

    def test_history_bucket_uses_latest_timestamp_after_backfill(self):
        base = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
        recent = self._state("u1", base.isoformat(), True, "Cool",
                             24.0, "Auto", 25.0)
        older = self._state("u1", (base - timedelta(minutes=1)).isoformat(),
                            True, "Cool", 22.0, "Auto", 23.0)
        self.store.record_history([recent, older])
        rows = self.store.pump_history(
            "u1", base - timedelta(hours=1), base + timedelta(hours=1), 1)
        self.assertEqual(rows, [(int(base.timestamp()), 25.0, 24.0)])

    def test_history_reads_wait_for_store_lock(self):
        base = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
        for query in (
            lambda: self.store.pump_history("u1", base, base + timedelta(hours=1), 10),
            lambda: self.store.outdoor_series("home", base, base + timedelta(hours=1), 10),
        ):
            entered = threading.Event()
            done = threading.Event()
            def read():
                entered.set()
                query()
                done.set()
            with self.store._lock:
                reader = threading.Thread(target=read)
                reader.start()
                self.assertTrue(entered.wait(1))
                self.assertFalse(done.wait(0.05))
            reader.join(timeout=1)
            self.assertTrue(done.is_set())

    def test_outdoor_upsert_and_series(self):
        base = datetime(2026, 10, 19, 0, 0, tzinfo=timezone.utc)
        vals = [(base + timedelta(hours=i), 10.0 + i) for i in range(5)]
        self.assertEqual(self.store.outdoor_upsert("home", vals), 5)
        # re-upserting the same rows is idempotent
        self.assertEqual(self.store.outdoor_upsert("home", vals), 5)
        rows = self.store.outdoor_series(
            "home", base - timedelta(hours=1), base + timedelta(hours=6), 288)
        self.assertEqual(len(rows), 5)
        self.assertEqual(
            self.store.outdoor_series("cottage", base,
                                      base + timedelta(hours=6), 288), [])

    def test_plan_series_and_command_marks(self):
        base = datetime(2026, 10, 19, 0, 0, tzinfo=timezone.utc)
        self.store.plan_upsert("u1", [(base + timedelta(hours=h), 21.0 + h)
                                      for h in range(3)])
        self.store.plan_upsert("u1", [(base, 20.0)])  # refresh, not duplicate
        rows = self.store.plan_series("u1", base, base + timedelta(hours=24),
                                      288)
        self.assertEqual([r[1] for r in rows], [20.0, 22.0, 23.0])
        self.assertEqual(self.store.plan_series(
            "u2", base, base + timedelta(hours=24), 288), [])
        first = self.store.add_event((base + timedelta(hours=1)).isoformat(),
                                     "u1", "X", "Ville", "setpoint", 22.0)
        self.store.finish_event(first, "confirmed")
        self.store.add_event((base + timedelta(hours=2)).isoformat(),
                             "u1", "X", "Ville", "fan", "Auto")  # pending
        failed = self.store.add_event(
            (base + timedelta(hours=3)).isoformat(), "u1", "X", "Ville",
            "power", True)
        self.store.finish_event(failed, "failed", "boom")
        marks = self.store.command_marks("u1", base, base + timedelta(hours=24))
        self.assertEqual([(m["action"], m["result"]) for m in marks],
                         [("setpoint", "confirmed"), ("power", "failed")])
        self.assertEqual(self.store.command_marks(
            "u2", base, base + timedelta(hours=24)), [])

    def test_energy_daily_cost_and_gaps(self):
        from decimal import Decimal
        from villa_kuppis.prices import PriceInterval
        from villa_kuppis.storage import PriceStore
        base = datetime(2026, 10, 19, 0, 0, tzinfo=timezone.utc)
        end = base + timedelta(days=2)
        prices_path = Path(self.tmp) / "prices.db"
        self.assertEqual(self.store.energy_daily("u1", prices_path, base, end),
                         [])
        # 10 EUR/MWh = 1 c/kWh for the first hour only.
        pstore = PriceStore(prices_path)
        try:
            pstore.replace_day("2026-10-19", [
                PriceInterval(int((base + timedelta(minutes=15 * i))
                                  .timestamp()), Decimal("10"))
                for i in range(4)], datetime(2026, 10, 19, 9, 0,
                                             tzinfo=timezone.utc))
        finally:
            pstore.close()
        self.store.energy_upsert("u1", [
            (base, 2.0), (base + timedelta(hours=1), 1.0),  # 2nd has no price
            (base + timedelta(days=1, hours=5), 3.0)])
        days = self.store.energy_daily("u1", prices_path, base, end)
        self.assertEqual([d["date"] for d in days],
                         ["2026-10-19", "2026-10-20"])
        self.assertEqual(days[0]["kwh"], 3.0)
        self.assertAlmostEqual(days[0]["cost_eur"], 0.02)
        self.assertFalse(days[0]["cost_complete"])
        self.assertIsNone(days[1]["cost_eur"])
        # Without any prices.db the energy still shows, cost unknown.
        days = self.store.energy_daily("u1", Path(self.tmp) / "none.db",
                                       base, end)
        self.assertEqual(days[0]["kwh"], 3.0)
        self.assertIsNone(days[0]["cost_eur"])

    def test_price_series_missing_db_is_none(self):
        base = datetime(2026, 10, 19, 0, 0, tzinfo=timezone.utc)
        self.assertIsNone(self.store.price_series(
            Path(self.tmp) / "nope.db", base,
            base + timedelta(hours=24), 288))

    def test_price_series_reads_prices_db_readonly(self):
        from decimal import Decimal
        from villa_kuppis.prices import PriceInterval
        from villa_kuppis.storage import PriceStore
        base = datetime(2026, 10, 19, 0, 0, tzinfo=timezone.utc)
        prices_path = Path(self.tmp) / "prices #1.db"
        pstore = PriceStore(prices_path)
        try:
            intervals = [
                PriceInterval(int((base + timedelta(minutes=15 * i))
                                  .timestamp()), Decimal("0.500000"))
                for i in range(96)]
            pstore.replace_day("2026-10-19", intervals,
                               datetime(2026, 10, 19, 9, 0,
                                        tzinfo=timezone.utc))
        finally:
            pstore.close()
        rows = self.store.price_series(prices_path, base,
                                       base + timedelta(hours=24), 288)
        self.assertIsNotNone(rows)
        self.assertLessEqual(len(rows), 288)
        for _ts, v in rows:
            self.assertAlmostEqual(v, 0.05)


class HistoryServiceTests(ServiceTestCase):
    """Service-level history payload, outdoor fetch thread (card F3.3)."""

    def test_history_payload_shape_and_price_null(self):
        base = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
        service, _clock = self._start(FakeClient(make_unit_payloads()))
        # seed 24 hourly rows in [base-24h, base)
        states = []
        for i in range(24):
            iso = (base - timedelta(hours=24) + timedelta(hours=i + 1)
                   ).isoformat()
            states.append({"id": "unit-1", "name": "Makuuhuone",
                           "power": True, "mode": "Cool",
                           "setpoint": 22.0 + i * 0.05, "fan": "Auto",
                           "room_temp": 20.0 + i * 0.1, "in_error": False,
                           "caps": None, "updated_at": iso})
        service.store.record_history(states)
        vals = [(base - timedelta(hours=24) + timedelta(hours=i + 1), 15.0)
                for i in range(24)]
        service.store.outdoor_upsert("home", vals)

        payload = service.history("unit-1", "day")
        self.assertEqual(payload["unit"], "unit-1")
        self.assertEqual(payload["range"], "day")
        self.assertTrue(payload["start"].startswith("2026-10-18T12:00:00"))
        self.assertTrue(payload["end"].startswith("2026-10-19T12:00:00"))
        for key in ("room_temp", "setpoint", "outdoor"):
            self.assertGreater(len(payload[key]), 0)
            self.assertLessEqual(len(payload[key]), 288)
            self.assertTrue(payload[key][0][0].endswith("+00:00"))
        self.assertIsNone(payload["price"])  # no prices.db in this data dir
        self.assertEqual(payload["plan"], [])
        self.assertEqual(payload["commands"], [])
        self.assertEqual(payload["energy"], [])

    def test_history_rejects_unknown_range(self):
        service, _clock = self._start(FakeClient(make_unit_payloads()))
        with self.assertRaises(ValueError):
            service.history("unit-1", "decade")

    def test_history_price_series_present_when_db_exists(self):
        from decimal import Decimal
        from villa_kuppis.prices import PriceInterval
        from villa_kuppis.storage import PriceStore
        base = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
        service, _clock = self._start(FakeClient(make_unit_payloads()))
        pstore = PriceStore(service._prices_db)
        try:
            day_start = int((base - timedelta(hours=24)).timestamp())
            intervals = [
                PriceInterval(day_start + i * 900, Decimal("0.500000"))
                for i in range(96)]
            pstore.replace_day("2026-10-19", intervals,
                               datetime(2026, 10, 19, 9, 0,
                                        tzinfo=timezone.utc))
        finally:
            pstore.close()
        payload = service.history("unit-1", "day")
        price = payload["price"]
        self.assertIsNotNone(price)
        self.assertGreater(len(price), 0)
        self.assertLessEqual(len(price), 288)
        for _ts, v in price:
            self.assertAlmostEqual(v, 0.05)

    def test_weather_thread_backfills_and_updates(self):
        class _Series:
            def __init__(self, values):
                self.values = values

        base = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
        values = [(base - timedelta(hours=2 - i), 10.0 + i) for i in range(3)]
        calls = []

        def fake_fetcher(lat, lon, start, end):
            calls.append((lat, lon, start, end))
            return _Series(values)

        service, _clock = self._start(
            FakeClient(make_unit_payloads()),
            weather_sites={"home": (60.1, 24.9)},
            weather_fetcher=fake_fetcher,
            weather_interval=0.05,
            weather_backfill_hours=24)
        self.wait_for(lambda: len(calls) >= 2, timeout=3.0,
                      message="weather thread did not fetch twice")
        # the first call is the 24h backfill
        self.assertAlmostEqual((calls[0][3] - calls[0][2]).total_seconds(),
                               86400, delta=1)
        rows = service.store.outdoor_series(
            "home", base - timedelta(hours=24), base + timedelta(hours=1), 288)
        self.assertEqual(len(rows), 3)  # idempotent upsert of 3 timestamps

    def test_weather_fetch_failure_does_not_stop_thread(self):
        class _Series:
            def __init__(self, values):
                self.values = values

        base = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
        calls = []

        def fake_fetcher(lat, lon, start, end):
            calls.append((start, end))
            if len(calls) == 1:
                raise RuntimeError("fmi down")
            return _Series([(base, 5.0)])

        service, _clock = self._start(
            FakeClient(make_unit_payloads()),
            weather_sites={"home": (60.1, 24.9)},
            weather_fetcher=fake_fetcher,
            weather_interval=0.05,
            weather_backfill_hours=1)
        # backfill failed once, but the loop must keep going and recover
        self.wait_for(lambda: len(calls) >= 2, timeout=3.0,
                      message="weather thread stopped after a failure")
        self.assertEqual(calls[0], calls[1])  # retry the full failed backfill

    def test_shutdown_drops_inflight_weather_result(self):
        entered = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        service = PumpService(Path(self.tmp), client=FakeClient([]))
        self._services.append(service)
        def fetcher(*_args):
            entered.set()
            release.wait(2)
            return type("Series", (), {"values": [(FakeClock()(), 5.0)]})()
        service._weather_fetcher = fetcher
        def fetch():
            try:
                service._fetch_outdoor("home", 60, 25, 1)
            finally:
                finished.set()
        with mock.patch.object(service.store, "outdoor_upsert") as upsert:
            worker = threading.Thread(target=fetch)
            worker.start()
            self.assertTrue(entered.wait(1))
            service.close()
            release.set()
            worker.join(timeout=2)
            self.assertTrue(finished.is_set())
            upsert.assert_not_called()


class HistoryPerfTests(unittest.TestCase):
    """A year of synthetic history must query fast (card F3.3)."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="vk-pump-perf-")
        self.tmp = self._tmpdir.name
        self.store = PumpStore(Path(self.tmp) / "pumps.db")

    def tearDown(self):
        self.store.close()
        self._tmpdir.cleanup()

    def test_year_of_history_queries_fast(self):
        base = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
        start = base - timedelta(days=365)
        n = int((base - start).total_seconds() // 300)  # 5-min resolution
        rows = [(int(start.timestamp() + i * 300), 20.0, 23.0)
                for i in range(n)]
        with self.store._lock:
            self.store._conn.executemany(
                "INSERT INTO pump_history (ts_utc, unit_id, power, mode, "
                "setpoint, fan, room_temp) VALUES (?, 'u1', 1, 'Cool', ?, "
                "'Auto', ?)", [(ts, 23.0, 20.0 + (i % 5) * 0.1)
                               for i, (ts, _a, _b) in enumerate(rows)])
            self.store._conn.commit()
        t0 = time.monotonic()
        out = self.store.pump_history("u1", start, base, 1000)
        elapsed = time.monotonic() - t0
        self.assertLessEqual(len(out), 1000)
        self.assertGreater(len(out), 0)
        self.assertLess(elapsed, 2.0, msg=f"year query took {elapsed:.2f}s")


if __name__ == "__main__":
    unittest.main()
