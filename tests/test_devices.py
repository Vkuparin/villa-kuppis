"""Tests for Home Assistant devices by area (devices.py).

The catalog rules run on synthetic registries; the gateway part uses the
fake Home Assistant WebSocket server from test_ha_gateway on localhost;
the web API runs against the offline demo service. No other network.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import unittest
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from villa_kuppis import devices, ha_gateway
from villa_kuppis.devices import (DeviceCatalog, DeviceError, DeviceStore, FakeDeviceSource,
                                  action_call, build)
from villa_kuppis.ha_gateway import BindingStore, HomeAssistantGateway

from test_ha_gateway import TOKEN, Clock, FakeHA, eventually


def state(entity_id, value, **attrs):
    return {"entity_id": entity_id, "state": value, "attributes": attrs,
            "last_changed": "2026-10-28T10:00:00+00:00"}


def registry(entities, devices_=(), areas=(("olohuone", "Olohuone"), ("piha", "Piha"))):
    return {"areas": [{"area_id": a, "name": n, "floor_id": None} for a, n in areas],
            "floors": [], "devices": list(devices_), "entities": list(entities)}


def entry(entity_id, area=None, device=None, **extra):
    return {"entity_id": entity_id, "id": "id-" + entity_id, "area_id": area,
            "device_id": device, "platform": "test", **extra}


class BuildTest(unittest.TestCase):
    def test_area_from_entity_or_its_device(self):
        reg = registry([entry("sensor.t", device="d1"), entry("light.l", area="piha", device="d1")],
                       [{"id": "d1", "area_id": "olohuone", "name": "Anturi"}])
        states = {"sensor.t": state("sensor.t", "21.5", device_class="temperature",
                                    unit_of_measurement="°C", friendly_name="Lämpö"),
                  "light.l": state("light.l", "on", brightness=128,
                                   supported_color_modes=["brightness"])}
        out = build(reg, states)
        by_area = {a["id"]: [d["entity_id"] for d in a["devices"]] for a in out["areas"]}
        self.assertEqual(by_area, {"olohuone": ["sensor.t"], "piha": ["light.l"]})
        sensor = out["areas"][0]["devices"][0]
        self.assertEqual((sensor["key"], sensor["kind"], sensor["value"]), ("id-sensor.t", "temperature", 21.5))
        light = out["areas"][1]["devices"][0]
        self.assertEqual((light["kind"], light["on"], light["brightness"]), ("light", True, 50))
        self.assertTrue(light["caps"]["dim"])

    def test_leaves_out_noise_unplaced_and_own_devices(self):
        reg = registry([
            entry("sensor.no_area"),
            entry("sensor.fw", area="olohuone", entity_category="diagnostic"),
            entry("switch.hidden", area="olohuone", hidden_by="user"),
            entry("switch.off", area="olohuone", disabled_by="user"),
            entry("climate.pump", area="olohuone"),
            entry("sensor.status", area="olohuone", platform="villa_kuppis_cottage"),
            entry("update.fw", area="olohuone"),
            entry("switch.plug", area="olohuone"),
        ])
        states = {e: state(e, "1") for e in ("sensor.no_area", "sensor.fw", "switch.hidden",
                                             "switch.off", "climate.pump", "sensor.status",
                                             "update.fw", "switch.plug", "sensor.unregistered")}
        out = build(reg, states, skip_entities={"climate.pump"})
        shown = [d["entity_id"] for a in out["areas"] for d in a["devices"]]
        self.assertEqual(shown, ["switch.plug"])

    def test_automations_are_listed_apart(self):
        reg = registry([entry("automation.lights")])
        out = build(reg, {"automation.lights": state("automation.lights", "on", friendly_name="Valot",
                                                     last_triggered="2026-10-28T09:00:00+00:00")})
        self.assertEqual([(a["name"], a["on"]) for a in out["automations"]], [("Valot", True)])

    def test_without_registry_nothing_is_guessed(self):
        out = build(None, {"light.l": state("light.l", "on")})
        self.assertEqual((out["registry"], out["areas"]), (False, []))


class ActionTest(unittest.TestCase):
    def device(self, entity_id, value, **attrs):
        reg = registry([entry(entity_id, area="olohuone")])
        return build(reg, {entity_id: state(entity_id, value, **attrs)})["areas"][0]["devices"][0]

    def test_power_and_confirmation(self):
        plug = self.device("switch.plug", "off", device_class="outlet")
        domain, service, data, confirm, _ = action_call(plug, "power", True)
        self.assertEqual((domain, service, data), ("switch", "turn_on", {}))
        self.assertFalse(confirm(state("switch.plug", "off")))
        self.assertTrue(confirm(state("switch.plug", "on")))

    def test_brightness_only_on_dimmable_lights(self):
        lamp = self.device("light.l", "on", supported_color_modes=["onoff"])
        with self.assertRaises(DeviceError):
            action_call(lamp, "brightness", 50)
        dim = self.device("light.d", "on", supported_color_modes=["color_temp"], brightness=10)
        _, service, data, confirm, _ = action_call(dim, "brightness", 40)
        self.assertEqual((service, data), ("turn_on", {"brightness_pct": 40}))
        self.assertTrue(confirm(state("light.d", "on", brightness=102)))
        with self.assertRaises(DeviceError):
            action_call(dim, "brightness", 0)

    def test_unavailable_and_unknown_actions_are_refused(self):
        with self.assertRaises(DeviceError):
            action_call(self.device("switch.p", "unavailable"), "power", True)
        with self.assertRaises(DeviceError):
            action_call(self.device("sensor.t", "21"), "power", True)
        with self.assertRaises(DeviceError):
            action_call(self.device("switch.p", "on"), "explode", True)

    def test_scene_runs_without_state_check(self):
        scene = self.device("scene.movie", "2026-10-28T09:00:00+00:00")
        domain, service, _, confirm, _ = action_call(scene, "run", True)
        self.assertEqual((domain, service, confirm), ("scene", "turn_on", None))


class CatalogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)
        self.store = DeviceStore(Path(self.tmp.name) / "pumps.db")
        self.source = FakeDeviceSource("home", now=lambda: self.now)
        self.catalog = DeviceCatalog("home", self.source, self.store, now=lambda: self.now)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def keys(self, view, area):
        return [d["key"] for a in view["areas"] if a["id"] == area for d in a["devices"]]

    def test_layout_order_hide_and_rename(self):
        view = self.catalog.view()
        names = [a["name"] for a in view["areas"]]
        self.assertEqual(names, ["Keittiö", "Olohuone", "Piha"])   # floor first, then name
        piha = self.keys(view, "piha")
        self.catalog.layout({"order": list(reversed(piha))})
        self.catalog.layout({"areas": ["piha", "olohuone", "keittio"]})
        self.catalog.layout({"key": piha[0], "hidden": True, "name": "  Jouluvalot   ulkona "})
        view = self.catalog.view()
        self.assertEqual([a["id"] for a in view["areas"]], ["piha", "olohuone", "keittio"])
        self.assertEqual(self.keys(view, "piha"), list(reversed(piha)))
        moved = [d for a in view["areas"] for d in a["devices"] if d["key"] == piha[0]][0]
        self.assertTrue(moved["hidden"])
        self.assertEqual(moved["name"], "Jouluvalot ulkona")
        self.catalog.layout({"key": piha[0], "name": ""})
        moved = [d for a in self.catalog.view()["areas"] for d in a["devices"] if d["key"] == piha[0]][0]
        self.assertEqual(moved["name"], moved["ha_name"])

    def test_devices_after_the_first_look_are_new_until_opened(self):
        first = self.catalog.view()
        self.assertFalse(any(d["new"] for a in first["areas"] for d in a["devices"]))
        self.source._registry["entities"].append(
            {"entity_id": "sensor.uusi", "id": "id-uusi", "area_id": "piha", "device_id": None})
        self.source._states["sensor.uusi"] = state("sensor.uusi", "3.5", device_class="temperature")
        new = [d for a in self.catalog.view()["areas"] for d in a["devices"] if d["key"] == "id-uusi"]
        self.assertTrue(new[0]["new"])
        self.catalog.layout({"key": "id-uusi", "seen": True})
        new = [d for a in self.catalog.view()["areas"] for d in a["devices"] if d["key"] == "id-uusi"]
        self.assertFalse(new[0]["new"])

    def test_command_is_sent_logged_and_confirmed(self):
        result = self.catalog.command("demo-switch.jouluvalot", "power", True, "Ville")
        self.assertEqual(result["state"], "confirmed")
        self.assertEqual(self.source.raw_states()["switch.jouluvalot"]["state"], "on")
        hub_events = self.store.events(10)
        self.assertEqual((hub_events[0]["actor"], hub_events[0]["requested"], hub_events[0]["result"]),
                         ("Ville", "Päälle", "confirmed"))
        with self.assertRaises(DeviceError):
            self.catalog.command("no-such-key", "power", True, "Ville")

    def test_history_records_changes_and_thins_old_rows(self):
        self.assertGreater(self.catalog.sample(), 0)
        self.assertEqual(self.catalog.sample(), 0)            # nothing changed
        self.now += timedelta(minutes=16)
        written = self.catalog.sample()                       # temperatures drift; refresh is due
        self.assertGreater(written, 0)
        self.catalog.command("demo-switch.jouluvalot", "power", True, "Ville")
        self.now += timedelta(minutes=1)
        self.catalog.sample()
        points = self.catalog.history("demo-switch.jouluvalot", 24)["points"]
        self.assertEqual([p[1] for p in points], [0.0, 1.0])
        # Two rows in one quarter hour become one mean once older than 30 days.
        start = int(self.now.timestamp()) // 900 * 900
        self.store.record("home", [("k", start, 1.0), ("k", start + 60, 3.0)])
        self.store.thin(start + 3600)
        rows = self.store.series("home", "k", start - 1, start + 900, 10)
        self.assertEqual([v for _, v in rows], [2.0])


class RoomSensorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)
        self.store = DeviceStore(Path(self.tmp.name) / "pumps.db")
        self.source = FakeDeviceSource("home", now=lambda: self.now)
        self.source.statistics = lambda entity_id, start: [
            (int(self.now.timestamp()) - 7200, 19.0), (int(self.now.timestamp()) - 3600, 19.5)]
        self.catalog = DeviceCatalog("home", self.source, self.store, now=lambda: self.now,
                                     rooms=lambda: [("unit-1", "Olohuone")])

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_a_room_can_use_its_own_thermometer(self):
        key = "demo-sensor.olohuone_temperature"
        self.assertIsNone(self.catalog.room_temperature("unit-1"))
        with self.assertRaises(DeviceError):
            self.catalog.set_room_sensor({"room": "unit-1", "key": "demo-switch.jouluvalot"})
        with self.assertRaises(DeviceError):
            self.catalog.set_room_sensor({"room": "nope", "key": key})
        self.catalog.set_room_sensor({"room": "unit-1", "key": key})
        value = self.source.raw_states()["sensor.olohuone_temperature"]["state"]
        self.assertEqual(self.catalog.room_temperature("unit-1"), float(value))
        self.assertEqual(self.catalog.view()["rooms"], [{"id": "unit-1", "name": "Olohuone", "sensor": key}])
        self.source._states["sensor.olohuone_temperature"]["state"] = "unavailable"
        self.source.raw_states = lambda: dict(self.source._states)
        self.assertIsNone(self.catalog.room_temperature("unit-1"))   # the unit's own reading then
        self.catalog.set_room_sensor({"room": "unit-1", "key": None})
        self.assertEqual(self.store.room_sensors("home"), {})

    def test_new_sensor_history_is_backfilled_once(self):
        self.catalog.sample()
        points = self.catalog.history("demo-sensor.olohuone_humidity", 24)["points"]
        self.assertEqual([p[1] for p in points][:2], [19.0, 19.5])
        self.assertEqual(len(points), 3)                       # two hours back and now
        self.catalog.sample()
        self.assertEqual(len(self.catalog.history("demo-sensor.olohuone_humidity", 24)["points"]), 3)

    def test_pump_service_uses_the_room_sensor(self):
        from villa_kuppis.pump_app import make_demo_service
        service = make_demo_service(self.tmp.name)
        try:
            service.store.upsert_units([{"id": "demo-1", "name": "Olohuone", "power": True, "mode": "cool", "setpoint": 23.0, "fan": "auto", "room_temp": 26.0, "in_error": False, "caps": None, "updated_at": "2026-10-28T12:00:00+00:00"}])
            catalog = service.devices.catalog("home")
            catalog.set_room_sensor({"room": "demo-1", "key": "demo-sensor.olohuone_temperature"})
            states = service.gateway._with_rooms([{"id": "demo-1", "room_temp": 26.0},
                                                  {"id": "demo-2", "room_temp": 27.0}])
            self.assertNotEqual(states[0]["room_temp"], 26.0)
            self.assertEqual(states[1]["room_temp"], 27.0)
        finally:
            service.close()


class GatewayRegistryTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.ha = LightHA(self.clock)
        await self.ha.start()
        self.ha.set_state("light.porch", "off", {"friendly_name": "Kuisti",
                                                 "supported_color_modes": ["onoff"]}, push=False)
        self.ha.registry = registry([entry("light.porch", area="piha")])
        self.store = BindingStore(Path(self.tmp.name) / "sites.db")
        self.patch = mock.patch.object(ha_gateway, "REGISTRY_DEBOUNCE", 0.01)
        self.patch.start()
        self.gateway = HomeAssistantGateway("cottage", self.ha.url, lambda: TOKEN, self.store,
                                            now=self.clock, command_timeout=0.5,
                                            confirm_timeout=0.5, retry_base=0.05,
                                            retry_max=0.2, resync_interval=10.0)
        self.task = asyncio.create_task(self.gateway.run())
        await eventually(lambda: self.gateway.registry() is not None)

    async def asyncTearDown(self):
        self.gateway.request_stop()
        await asyncio.wait_for(self.task, 5)
        await self.ha.stop()
        self.patch.stop()
        self.tmp.cleanup()

    async def test_registry_events_and_new_entities_reload_the_registry(self):
        reads = self.ha.registry_reads
        self.ha.registry["areas"].append({"area_id": "sauna", "name": "Sauna", "floor_id": None})
        self.ha.fire("area_registry_updated", {"action": "create", "area_id": "sauna"})
        await eventually(lambda: any(a["area_id"] == "sauna"
                                     for a in self.gateway.registry()["areas"]))
        self.assertGreater(self.ha.registry_reads, reads)
        # A brand-new entity reloads the registry without any registry event.
        self.ha.registry["entities"].append(entry("sensor.sauna", area="sauna"))
        self.ha.set_state("sensor.sauna", "60", {"device_class": "temperature"})
        await eventually(lambda: any(e["entity_id"] == "sensor.sauna"
                                     for e in self.gateway.registry()["entities"]))
        out = build(self.gateway.registry(), self.gateway.raw_states())
        self.assertEqual({a["id"]: len(a["devices"]) for a in out["areas"]},
                         {"olohuone": 0, "piha": 1, "sauna": 1})

    async def test_entity_command_is_confirmed_by_its_state(self):
        done = self.gateway.submit_entity("light.porch", "light", "turn_on", {}, "Ville",
                                          lambda raw: raw.get("state") == "on")
        self.assertEqual(done["state"], "pending")
        result = await self.gateway.wait(done["request_id"], 2)
        self.assertEqual(result["state"], "confirmed")
        self.assertEqual(self.ha.calls[-1], ("light", "turn_on", "light.porch", {}))
        missing = self.gateway.submit_entity("light.gone", "light", "turn_on", {}, "Ville")
        self.assertEqual(missing["state"], "failed")

    async def test_statistics_are_hourly_means(self):
        start_ms = 1793000000000
        self.ha.answers["recorder/statistics_during_period"] = {"sensor.sauna": [
            {"start": start_ms, "mean": 20.5}, {"start": start_ms + 3600000, "mean": None},
            {"start": "2026-10-27T10:00:00+00:00", "mean": 19.0}]}
        points = await asyncio.get_running_loop().run_in_executor(
            None, lambda: self.gateway.statistics("sensor.sauna", self.clock()))
        self.assertEqual(points, [(start_ms // 1000, 20.5), (1793095200, 19.0)])
        self.assertEqual(self.ha.asked[-1]["statistic_ids"], ["sensor.sauna"])

    async def test_area_target_call(self):
        await asyncio.get_running_loop().run_in_executor(
            None, lambda: self.gateway.call("light", "turn_off", {}, target={"area_id": "piha"}))
        self.assertEqual(self.ha.targets[-1], {"area_id": "piha"})


class LightHA(FakeHA):
    """FakeHA where turn_on/turn_off switch lights and plugs on and off."""

    def __init__(self, clock):
        super().__init__(clock)
        self.targets = []

    async def _call(self, ws, data):
        self.targets.append(data.get("target"))
        await super()._call(ws, data)

    async def _apply(self, entity_id, service, values):
        if service in ("turn_on", "turn_off") and not entity_id.startswith("climate."):
            self.set_state(entity_id, "on" if service == "turn_on" else "off")
            return
        await super()._apply(entity_id, service, values)


class DeviceApiTest(unittest.TestCase):
    """The web API over the offline demo service."""

    def setUp(self):
        from villa_kuppis.pump_app import PumpAppServer, make_demo_service
        self.tmp = tempfile.TemporaryDirectory()
        self.service = make_demo_service(self.tmp.name)
        self.server = PumpAppServer(self.service, port=0, demo=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.service.close()
        self.tmp.cleanup()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as r:
            return json.loads(r.read())

    def post(self, path, body):
        req = urllib.request.Request(self.base + path, json.dumps(body).encode(),
                                     {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read())

    def test_view_command_layout_and_events(self):
        view = self.get("/api/devices?site=cottage")
        self.assertEqual([a["name"] for a in view["areas"]], ["Kuisti", "Sauna"])
        status, result = self.post("/api/devices/command", {
            "site": "cottage", "key": "demo-switch.jouluvalot", "action": "power", "value": True})
        self.assertEqual((status, result["state"]), (200, "confirmed"))
        status, _ = self.post("/api/devices/layout", {"site": "cottage", "key": "demo-light.kuisti",
                                                      "hidden": True})
        self.assertEqual(status, 200)
        view = self.get("/api/devices?site=cottage")
        porch = [d for a in view["areas"] for d in a["devices"] if d["key"] == "demo-light.kuisti"]
        self.assertTrue(porch[0]["hidden"])
        events = self.get("/api/events")
        self.assertEqual(events[0]["unit_name"], "Mökki: Jouluvalot")
        status, err = self.post("/api/devices/command", {
            "site": "cottage", "key": "demo-sensor.sauna_temperature", "action": "power", "value": True})
        self.assertEqual((status, err["error"]), (400, "bad_body"))
        status, err = self.post("/api/devices/command", {"site": "mars", "key": "x", "action": "power"})
        self.assertEqual(status, 400)
        self.assertFalse(self.get("/api/devices?site=mars")["configured"])

    def test_lights_off_for_an_area(self):
        status, _ = self.post("/api/devices/lights_off", {"site": "home", "area": "olohuone"})
        self.assertEqual(status, 200)
        view = self.get("/api/devices?site=home")
        lamp = [d for a in view["areas"] for d in a["devices"] if d["entity_id"] == "light.olohuone_katto"]
        self.assertFalse(lamp[0]["on"])


if __name__ == "__main__":
    unittest.main()
