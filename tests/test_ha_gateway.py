"""Tests for the Home Assistant gateway (card H1.2).

A fake Home Assistant WebSocket server runs on localhost and speaks the
real protocol subset the gateway uses: auth, get_states, subscribe_events
(state_changed) and call_service. Clocks are injected; timeouts are short
so the file runs in a few seconds. No network beyond 127.0.0.1.
"""

from __future__ import annotations

import asyncio
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiohttp import web

from villa_kuppis.ha_gateway import (
    BindingError, BindingStore, HomeAssistantGateway, capabilities, validate,
)

TOKEN = "test-token"
CLIMATE_FEATURES = 1 | 8 | 128 | 256


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class FakeHA:
    """Just enough of Home Assistant's WebSocket API."""

    def __init__(self, clock: Clock):
        self.clock = clock
        self.token = TOKEN
        self.states: dict[str, dict] = {}
        self.sockets: list[tuple[web.WebSocketResponse, int | None]] = []
        self.calls: list[tuple[str, str, str, dict]] = []
        self.fail: dict[str, tuple[str, str]] = {}   # service -> (code, message)
        self.apply = True
        self.event_delay = 0.0
        self.hang = False
        self.connections = 0
        self._tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/api/websocket", self._handler)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.url = f"ws://127.0.0.1:{port}/api/websocket"

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await self.drop()
        await self._runner.cleanup()

    def set_state(self, entity_id: str, state: str, attrs: dict | None = None,
                  *, at: datetime | None = None, push: bool = True) -> dict:
        ts = (at or self.clock()).isoformat()
        old = self.states.get(entity_id)
        new = {
            "entity_id": entity_id, "state": state,
            "attributes": dict(attrs if attrs is not None else (old or {}).get("attributes", {})),
            "last_changed": ts, "last_updated": ts, "last_reported": ts,
        }
        self.states[entity_id] = new
        if push:
            self.push(entity_id, old, new)
        return new

    def remove(self, entity_id: str) -> None:
        old = self.states.pop(entity_id)
        self.push(entity_id, old, None)

    def push(self, entity_id: str, old, new) -> None:
        for ws, sub_id in list(self.sockets):
            if sub_id is not None and not ws.closed:
                task = asyncio.ensure_future(ws.send_json({
                    "id": sub_id, "type": "event",
                    "event": {"event_type": "state_changed",
                              "data": {"entity_id": entity_id,
                                       "old_state": old, "new_state": new}},
                }))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

    async def drop(self) -> None:
        for ws, _ in list(self.sockets):
            await ws.close()

    async def _handler(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"type": "auth_required", "ha_version": "2026.10.0"})
        auth = await ws.receive_json()
        if auth.get("access_token") != self.token:
            await ws.send_json({"type": "auth_invalid", "message": "Invalid access token"})
            await ws.close()
            return ws
        await ws.send_json({"type": "auth_ok", "ha_version": "2026.10.0"})
        self.connections += 1
        entry = [ws, None]
        self.sockets.append(entry)
        try:
            async for msg in ws:
                data = msg.json()
                kind, msg_id = data["type"], data["id"]
                if kind == "get_states":
                    await ws.send_json({"id": msg_id, "type": "result", "success": True,
                                        "result": list(self.states.values())})
                elif kind == "subscribe_events":
                    entry[1] = msg_id
                    await ws.send_json({"id": msg_id, "type": "result", "success": True,
                                        "result": None})
                elif kind == "call_service":
                    await self._call(ws, data)
        finally:
            self.sockets.remove(entry)
        return ws

    async def _call(self, ws, data) -> None:
        entity_id = data["target"]["entity_id"]
        service = data["service"]
        self.calls.append((data["domain"], service, entity_id, data["service_data"]))
        if self.hang:
            return
        if service in self.fail:
            code, message = self.fail[service]
            await ws.send_json({"id": data["id"], "type": "result", "success": False,
                                "error": {"code": code, "message": message}})
            return
        await ws.send_json({"id": data["id"], "type": "result", "success": True,
                            "result": {"context": {}}})
        if self.apply:
            task = asyncio.ensure_future(self._apply(entity_id, service, data["service_data"]))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _apply(self, entity_id, service, values) -> None:
        if self.event_delay:
            await asyncio.sleep(self.event_delay)
        old = self.states[entity_id]
        state, attrs = old["state"], dict(old["attributes"])
        if service == "set_temperature":
            attrs["temperature"] = values["temperature"]
        elif service == "turn_off":
            state = "off"
        elif service == "turn_on":
            state = "cool"
        elif service == "set_hvac_mode":
            state = values["hvac_mode"]
        elif service == "set_fan_mode":
            attrs["fan_mode"] = values["fan_mode"]
        elif service == "set_value":
            state = str(values["value"])
        self.set_state(entity_id, state, attrs)


def climate_attrs(**extra) -> dict:
    attrs = {"friendly_name": "Olohuone", "temperature": 21.0,
             "current_temperature": 22.5, "min_temp": 10.0, "max_temp": 31.0,
             "target_temp_step": 0.5, "hvac_modes": ["off", "cool", "heat", "dry"],
             "fan_modes": ["auto", "one", "two"], "fan_mode": "auto",
             "supported_features": CLIMATE_FEATURES}
    attrs.update(extra)
    return attrs


async def eventually(predicate, timeout: float = 3.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.01)


class GatewayTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.ha = FakeHA(self.clock)
        await self.ha.start()
        self.ha.set_state("climate.olohuone", "cool", climate_attrs(), push=False)
        self.ha.set_state("sensor.iso_huone_temperature", "5.4",
                          {"unit_of_measurement": "°C", "device_class": "temperature"},
                          push=False)
        self.ha.set_state("input_number.iso_huone_target", "5.0",
                          {"min": 5.0, "max": 25.0, "step": 0.5}, push=False)
        self.store = BindingStore(Path(self.tmp.name) / "bindings.db")
        self.store.bind("home", "pump-living", "climate.olohuone", "climate")
        self.store.bind("home", "large-temp", "sensor.iso_huone_temperature", "sensor", "Iso huone")
        self.store.bind("home", "large-target", "input_number.iso_huone_target", "number")
        self.done: list[dict] = []
        self.gateway = None
        self.task = None

    def make_gateway(self, **kwargs) -> HomeAssistantGateway:
        options = dict(now=self.clock, command_timeout=0.5, confirm_timeout=0.3,
                       retry_base=0.05, retry_max=0.2, resync_interval=10.0,
                       on_command_done=self.done.append)
        options.update(kwargs)
        return HomeAssistantGateway("home", self.ha.url, lambda: TOKEN, self.store, **options)

    async def start_gateway(self, **kwargs) -> HomeAssistantGateway:
        self.gateway = self.make_gateway(**kwargs)
        self.task = asyncio.create_task(self.gateway.run())
        await eventually(lambda: self.gateway.health()["state"] == "ok")
        return self.gateway

    async def asyncTearDown(self):
        if self.task is not None:
            self.gateway.request_stop()
            await asyncio.wait_for(self.task, 3.0)
        await self.ha.stop()
        self.store.close()
        self.tmp.cleanup()

    async def command(self, device_id, action, value) -> dict:
        cmd = self.gateway.submit(device_id, action, value, "test")
        if cmd["state"] == "pending":
            cmd = await self.gateway.wait(cmd["request_id"], 3.0)
        return cmd


class SnapshotTests(GatewayTestCase):
    async def test_snapshot_maps_stable_ids_to_readings(self):
        gw = await self.start_gateway()
        snap = gw.snapshot()
        self.assertEqual(set(snap), {"pump-living", "large-temp", "large-target"})
        pump = snap["pump-living"]
        self.assertEqual(pump["entity_id"], "climate.olohuone")
        self.assertEqual((pump["power"], pump["mode"], pump["setpoint"], pump["room_temp"], pump["fan"]),
                         (True, "cool", 21.0, 22.5, "auto"))
        self.assertEqual(pump["caps"]["min"], 10.0)
        self.assertTrue(pump["caps"]["power"])
        self.assertEqual(pump["status"], "ok")
        self.assertIsNone(pump["pending"])
        self.assertEqual(snap["large-temp"]["value"], 5.4)
        self.assertEqual(snap["large-temp"]["label"], "Iso huone")
        self.assertEqual(snap["large-target"]["value"], 5.0)
        health = gw.health()
        self.assertEqual(health["ha_version"], "2026.10.0")
        self.assertEqual(health["missing"], [])

    async def test_discover_lists_bindable_entities_only(self):
        self.ha.set_state("light.kitchen", "on", {}, push=False)
        self.ha.set_state("climate.makuuhuone", "off", climate_attrs(), push=False)
        gw = await self.start_gateway()
        found = {e["entity_id"]: e for e in gw.discover()}
        self.assertNotIn("light.kitchen", found)
        self.assertIsNone(found["climate.makuuhuone"]["device_id"])
        self.assertEqual(found["climate.olohuone"]["device_id"], "pump-living")

    async def test_state_changed_event_updates_reading(self):
        gw = await self.start_gateway()
        self.ha.set_state("sensor.iso_huone_temperature", "6.1")
        await eventually(lambda: gw.snapshot()["large-temp"]["value"] == 6.1)

    async def test_older_event_is_ignored(self):
        gw = await self.start_gateway()
        older = self.clock() - timedelta(minutes=5)
        self.ha.set_state("sensor.iso_huone_temperature", "4.0", at=older)
        self.ha.set_state("sensor.iso_huone_temperature", "6.5")
        await eventually(lambda: gw.snapshot()["large-temp"]["value"] == 6.5)
        self.assertEqual(gw.snapshot()["large-temp"]["value"], 6.5)
        # A late copy of an earlier report must not overwrite the newer one.
        self.ha.push("sensor.iso_huone_temperature", None, {
            "entity_id": "sensor.iso_huone_temperature", "state": "3.0", "attributes": {},
            "last_updated": older.isoformat(), "last_reported": older.isoformat()})
        self.ha.set_state("input_number.iso_huone_target", "6.0")
        await eventually(lambda: gw.snapshot()["large-target"]["value"] == 6.0)
        self.assertEqual(gw.snapshot()["large-temp"]["value"], 6.5)

    async def test_unavailable_and_stale_readings(self):
        gw = await self.start_gateway(entity_stale_after=600, site_stale_after=120)
        self.ha.set_state("sensor.iso_huone_temperature", "unavailable")
        await eventually(lambda: gw.snapshot()["large-temp"]["status"] == "unavailable")
        self.assertIsNone(gw.snapshot()["large-temp"]["value"])
        self.clock.advance(601)
        self.assertEqual(gw.snapshot()["pump-living"]["status"], "stale")
        self.assertEqual(gw.health()["state"], "stale")

    async def test_missing_or_renamed_entity_is_a_mapping_error(self):
        gw = await self.start_gateway()
        self.ha.remove("climate.olohuone")
        self.ha.set_state("climate.olohuone_2", "cool", climate_attrs())
        await eventually(lambda: gw.snapshot()["pump-living"]["status"] == "missing")
        self.assertEqual(gw.health()["missing"], ["pump-living"])
        cmd = gw.submit("pump-living", "setpoint", 23.0, "test")
        self.assertEqual(cmd["state"], "failed")
        self.assertIn("climate.olohuone is missing", cmd["detail"])
        self.assertEqual(self.ha.calls, [])

    async def test_bound_entity_absent_at_start_is_missing(self):
        self.store.bind("home", "small-temp", "sensor.pieni_huone_temperature", "sensor")
        gw = await self.start_gateway()
        self.assertEqual(gw.snapshot()["small-temp"]["status"], "missing")
        self.assertEqual(gw.health()["missing"], ["small-temp"])


class ConnectionTests(GatewayTestCase):
    async def test_wrong_token_is_reported_and_retried_slowly(self):
        self.ha.token = "rotated"
        self.gateway = self.make_gateway()
        self.task = asyncio.create_task(self.gateway.run())
        await eventually(lambda: self.gateway.health()["state"] == "auth_failed")
        self.assertIn("access token refused", self.gateway.health()["error"])
        self.assertEqual(self.gateway.retry_delay(1, auth_failed=True), 0.2)
        self.assertEqual(self.gateway.snapshot()["pump-living"]["status"], "unknown")
        cmd = self.gateway.submit("pump-living", "setpoint", 22.0, "test")
        self.assertEqual(cmd["state"], "failed")
        # The token provider is read on every connect, so a rotated token works.
        self.ha.token = TOKEN
        await eventually(lambda: self.gateway.health()["state"] == "ok")

    async def test_reconnect_takes_a_fresh_snapshot(self):
        gw = await self.start_gateway()
        await self.ha.drop()
        await eventually(lambda: gw.health()["state"] in ("connecting", "disconnected"))
        self.assertEqual(gw.snapshot()["large-temp"]["status"], "stale")
        # Changed while the gateway was away: no event reaches it.
        self.ha.set_state("sensor.iso_huone_temperature", "7.2", push=False)
        await eventually(lambda: gw.health()["state"] == "ok")
        await eventually(lambda: gw.snapshot()["large-temp"]["value"] == 7.2)
        self.assertEqual(gw.snapshot()["large-temp"]["status"], "ok")
        self.assertGreaterEqual(self.ha.connections, 2)

    async def test_periodic_resync_catches_lost_events(self):
        gw = await self.start_gateway(resync_interval=0.1)
        self.ha.set_state("input_number.iso_huone_target", "8.0", push=False)
        await eventually(lambda: gw.snapshot()["large-target"]["value"] == 8.0)

    async def test_retry_delay_backs_off(self):
        gw = self.make_gateway(retry_base=5, retry_max=300)
        self.assertEqual([gw.retry_delay(n) for n in (1, 2, 3, 8)], [5, 10, 20, 300])


class CommandTests(GatewayTestCase):
    async def test_setpoint_confirmed_by_state_event(self):
        gw = await self.start_gateway()
        cmd = gw.submit("pump-living", "setpoint", 23.5, "Ville")
        self.assertEqual(cmd["state"], "pending")
        self.assertEqual(gw.snapshot()["pump-living"]["pending"]["value"], 23.5)
        cmd = await gw.wait(cmd["request_id"], 3.0)
        self.assertEqual(cmd["state"], "confirmed")
        self.assertEqual(self.ha.calls, [("climate", "set_temperature", "climate.olohuone",
                                          {"temperature": 23.5})])
        self.assertEqual(gw.snapshot()["pump-living"]["setpoint"], 23.5)
        self.assertIsNone(gw.snapshot()["pump-living"]["pending"])
        self.assertEqual(self.done[-1]["request_id"], cmd["request_id"])
        self.assertEqual(self.done[-1]["actor"], "Ville")

    async def test_power_mode_fan_and_number(self):
        await self.start_gateway()
        self.assertEqual((await self.command("pump-living", "power", False))["state"], "confirmed")
        self.assertEqual((await self.command("pump-living", "mode", "cool"))["state"], "confirmed")
        self.assertEqual((await self.command("pump-living", "fan", "two"))["state"], "confirmed")
        self.assertEqual((await self.command("large-target", "value", 7.5))["state"], "confirmed")
        services = [(d, s) for d, s, _, _ in self.ha.calls]
        self.assertEqual(services, [("climate", "turn_off"), ("climate", "set_hvac_mode"),
                                    ("climate", "set_fan_mode"), ("input_number", "set_value")])

    async def test_value_already_reported_needs_no_call(self):
        gw = await self.start_gateway()
        cmd = gw.submit("pump-living", "setpoint", 21.0, "test")
        self.assertEqual(cmd["state"], "confirmed")
        self.assertEqual(self.ha.calls, [])

    async def test_invalid_commands_fail_without_a_call(self):
        gw = await self.start_gateway()
        cases = [
            ("pump-living", "setpoint", 35.0, "between 10 and 31"),
            ("pump-living", "setpoint", 21.3, "steps of 0.5"),
            ("pump-living", "setpoint", True, "must be a number"),
            ("pump-living", "mode", "auto", "not supported"),
            ("pump-living", "fan", "five", "not supported"),
            ("pump-living", "value", 3, "action must be one of"),
            ("large-temp", "value", 3, "read-only"),
            ("large-target", "value", 4.0, "between 5 and 25"),
            ("nobody", "power", True, "not bound"),
        ]
        for device_id, action, value, detail in cases:
            with self.subTest(device=device_id, action=action, value=value):
                cmd = gw.submit(device_id, action, value, "test")
                self.assertEqual(cmd["state"], "failed")
                self.assertIn(detail, cmd["detail"])
        self.assertEqual(self.ha.calls, [])

    async def test_unavailable_device_refuses_commands(self):
        gw = await self.start_gateway()
        self.ha.set_state("climate.olohuone", "unavailable", climate_attrs())
        await eventually(lambda: gw.snapshot()["pump-living"]["status"] == "unavailable")
        cmd = gw.submit("pump-living", "setpoint", 22.0, "test")
        self.assertEqual(cmd["state"], "failed")
        self.assertIn("unavailable", cmd["detail"])

    async def test_home_assistant_error_fails_the_command(self):
        await self.start_gateway()
        self.ha.fail["set_temperature"] = ("home_assistant_error", "cloud returned 500")
        cmd = await self.command("pump-living", "setpoint", 22.0)
        self.assertEqual(cmd["state"], "failed")
        self.assertIn("cloud returned 500", cmd["detail"])

    async def test_no_answer_is_unknown(self):
        await self.start_gateway()
        self.ha.hang = True
        cmd = await self.command("pump-living", "setpoint", 22.0)
        self.assertEqual(cmd["state"], "unknown")
        self.assertIn("may have been applied", cmd["detail"])

    async def test_connection_lost_mid_command_is_unknown(self):
        gw = await self.start_gateway()
        self.ha.hang = True
        cmd = gw.submit("pump-living", "setpoint", 22.0, "test")
        await eventually(lambda: self.ha.calls)
        await self.ha.drop()
        cmd = await gw.wait(cmd["request_id"], 3.0)
        self.assertEqual(cmd["state"], "unknown")

    async def test_delayed_report_confirms_an_unknown_command(self):
        gw = await self.start_gateway()
        self.ha.event_delay = 0.6   # longer than confirm_timeout
        cmd = await self.command("pump-living", "setpoint", 24.0)
        self.assertEqual(cmd["state"], "unknown")
        self.assertIn("did not report the new value", cmd["detail"])
        await eventually(lambda: gw.command(cmd["request_id"])["state"] == "confirmed")
        self.assertEqual(gw.command(cmd["request_id"])["detail"], "confirmed by a later report")

    async def test_unknown_command_confirmed_after_reconnect(self):
        gw = await self.start_gateway()
        self.ha.apply = False
        cmd = await self.command("pump-living", "setpoint", 24.0)
        self.assertEqual(cmd["state"], "unknown")
        await self.ha.drop()
        # The change happened while disconnected; the next snapshot shows it.
        self.ha.set_state("climate.olohuone", "cool", climate_attrs(temperature=24.0), push=False)
        await eventually(lambda: gw.command(cmd["request_id"])["state"] == "confirmed")

    async def test_duplicate_returns_the_pending_command_and_conflict_fails(self):
        gw = await self.start_gateway()
        self.ha.event_delay = 0.1
        first = gw.submit("pump-living", "setpoint", 22.0, "test")
        again = gw.submit("pump-living", "setpoint", 22.0, "test")
        other = gw.submit("pump-living", "setpoint", 23.0, "test")
        self.assertEqual(again["request_id"], first["request_id"])
        self.assertEqual(other["state"], "failed")
        self.assertIn("pending", other["detail"])
        self.assertEqual((await gw.wait(first["request_id"], 3.0))["state"], "confirmed")
        self.assertEqual(len(self.ha.calls), 1)
        # Once the first one is done, the next command goes through.
        self.assertEqual((await self.command("pump-living", "setpoint", 23.0))["state"], "confirmed")

    async def test_not_connected_fails_at_once(self):
        gw = self.make_gateway()
        cmd = gw.submit("pump-living", "setpoint", 22.0, "test")
        self.assertEqual(cmd["state"], "failed")
        self.assertIn("not connected", cmd["detail"])


class ThreadTests(GatewayTestCase):
    async def test_thread_runner_accepts_commands_from_other_threads(self):
        gw = self.make_gateway()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, gw.start)
        try:
            await eventually(lambda: gw.health()["state"] == "ok")
            result = {}

            def from_http_thread():
                result["cmd"] = gw.submit("pump-living", "setpoint", 22.5, "web")

            thread = threading.Thread(target=from_http_thread)
            thread.start()
            thread.join()
            request_id = result["cmd"]["request_id"]
            await eventually(lambda: gw.command(request_id)["state"] == "confirmed")
        finally:
            await loop.run_in_executor(None, gw.stop)
        self.assertEqual(gw.health()["state"], "stopped")


class BindingStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "b.db"
        self.store = BindingStore(self.path)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_domain_must_match_kind(self):
        with self.assertRaises(BindingError):
            self.store.bind("cottage", "large-heater-1", "sensor.mill", "climate")
        with self.assertRaises(BindingError):
            self.store.bind("cottage", "x", "climate.mill", "switch")
        self.store.bind("cottage", "target", "number.mill_target", "number")

    def test_one_entity_one_device_per_site(self):
        self.store.bind("cottage", "a", "climate.mill_1", "climate")
        with self.assertRaises(BindingError):
            self.store.bind("cottage", "b", "climate.mill_1", "climate")
        # The same entity id at another site is a different entity.
        self.store.bind("home", "b", "climate.mill_1", "climate")

    def test_rebinding_and_persistence(self):
        self.store.bind("cottage", "a", "climate.mill_1", "climate", "Iso huone 1")
        self.store.bind("cottage", "a", "climate.mill_9", "climate", "Iso huone 1")
        self.store.close()
        self.store = BindingStore(self.path)
        [binding] = self.store.bindings("cottage")
        self.assertEqual(binding.entity_id, "climate.mill_9")
        self.assertTrue(self.store.unbind("cottage", "a"))
        self.assertEqual(self.store.bindings("cottage"), [])


class PureFunctionTests(unittest.TestCase):
    def test_capabilities_report_only_supported_features(self):
        caps = capabilities("climate", {"supported_features": 1, "min_temp": 5, "max_temp": 35})
        self.assertTrue(caps["setpoint"])
        self.assertFalse(caps["power"])
        self.assertFalse(caps["fan"])
        self.assertEqual((caps["min"], caps["max"], caps["step"]), (5.0, 35.0, None))

    def test_setpoint_without_step_accepts_any_value_in_range(self):
        state = {"kind": "climate", "available": True, "mode": "heat",
                 "caps": capabilities("climate", {"supported_features": 1,
                                                  "min_temp": 5, "max_temp": 35})}
        self.assertEqual(validate(state, "setpoint", 5.3), (True, 5.3, ""))
        self.assertFalse(validate(state, "power", True)[0])


if __name__ == "__main__":
    unittest.main()
