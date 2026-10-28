"""Cottage readings, history and events in Villa Kuppis (card H2.3).

Offline: a fake Home Assistant WebSocket server on 127.0.0.1 (reused from
test_ha_gateway), injected clocks, a stub fake gateway for the HTTP layer
and the demo. Nothing leaves the machine.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests.test_ha_gateway import TOKEN, Clock, FakeHA, climate_attrs, eventually
from villa_kuppis import cottage, sites
from villa_kuppis.cottage import (ACTOR_CONTROLLER, ACTOR_MANUAL, CottageSite,
                                  CottageStore, FakeSiteGateway, parse_sites,
                                  role_of)
from villa_kuppis.credential_file import read_token_file
from villa_kuppis.ha_gateway import Binding, BindingStore, HomeAssistantGateway
from villa_kuppis.pump_app import PumpAppServer, make_demo_service, render_page
from villa_kuppis.pump_probe import FakeClient, _unit_payload
from villa_kuppis.pump_service import HISTORY_INTERVAL_SECONDS, PumpService
from villa_kuppis.runtime import Config
from villa_kuppis.credential_file import CredentialError

POSIX = unittest.skipIf(os.name == "nt", "POSIX file modes")
SECRET = "hunter2-secret-token"


def write_token(folder, text=SECRET, mode=0o600, name="cottage-token"):
    path = Path(folder) / name
    path.write_text(text, encoding="utf-8")
    os.chmod(path, mode)
    return path


def write_config(folder, **sites_field):
    data = Path(folder) / "data"
    values = {"data_dir": str(data), "backup_dir": str(Path(folder) / "backups")}
    if sites_field:
        values["sites"] = sites_field
    path = Path(folder) / "service.json"
    path.write_text(json.dumps(values))
    return path


class TokenFileTests(unittest.TestCase):
    def test_reads_a_plain_token(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(read_token_file(write_token(temp, SECRET + "\n")), SECRET)

    @POSIX
    def test_group_or_world_access_is_refused_without_leaking_the_token(self):
        with tempfile.TemporaryDirectory() as temp:
            for mode in (0o640, 0o604, 0o666):
                with self.assertRaises(CredentialError) as caught:
                    read_token_file(write_token(temp, SECRET, mode))
                self.assertNotIn(SECRET, str(caught.exception))

    def test_bad_files_never_echo_contents(self):
        with tempfile.TemporaryDirectory() as temp:
            for text in ("", "  \n", f"{SECRET}\nsecond-line"):
                with self.assertRaises(CredentialError) as caught:
                    read_token_file(write_token(temp, text))
                self.assertNotIn(SECRET, str(caught.exception))
            for path in (Path(temp) / "missing", "relative/token"):
                with self.assertRaises(CredentialError):
                    read_token_file(path)


class SitesConfigTests(unittest.TestCase):
    def test_sites_are_optional_and_validated(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(Config.load(write_config(temp)).sites, ())
            token = str(Path(temp) / "token")
            good = {"url": "ws://100.64.0.5:8123/api/websocket", "token_file": token}
            ((site_id, site),) = Config.load(write_config(temp, cottage=good)).sites
            self.assertEqual((site_id, site.url, site.token_file),
                             ("cottage", good["url"], Path(token)))
            self.assertEqual(parse_sites({"cottage": {**good, "url": "wss://ha.example.invalid/api/websocket"}})[0][0],
                             "cottage")
            bad = [
                {"sauna": good},
                {"cottage": {**good, "url": "http://100.64.0.5:8123"}},
                {"cottage": {**good, "url": "ws://"}},
                {"cottage": {**good, "url": 5}},
                {"cottage": {**good, "token_file": "relative/token"}},
                {"cottage": {**good, "token_file": 5}},
                {"cottage": {"url": good["url"]}},
                {"cottage": {**good, "extra": 1}},
                [good],
            ]
            for sites_field in bad:
                with self.subTest(sites_field=sites_field), self.assertRaises(ValueError):
                    parse_sites(sites_field)


class SitesCliTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.ha = FakeHA(self.clock)
        await self.ha.start()
        self.token = write_token(self.tmp.name, TOKEN)
        self.config = write_config(self.tmp.name, cottage={
            "url": self.ha.url, "token_file": str(self.token)})

    async def asyncTearDown(self):
        await self.ha.stop()
        self.tmp.cleanup()

    async def run_cli(self, *argv):
        lines: list[str] = []
        code = await asyncio.to_thread(
            sites.main, ["--config", str(self.config), *argv], lines.append)
        return code, lines

    async def test_bind_list_and_unbind(self):
        code, out = await self.run_cli(
            "bind", "cottage", "large-temperature", "sensor.alpstuga", "sensor",
            "--room", "large", "--label", "ALPSTUGA")
        self.assertEqual(code, 0, out)
        code, _ = await self.run_cli("bind", "cottage", "large-status",
                                     "sensor.villa_kuppis_large_target", "sensor",
                                     "--room", "large")
        self.assertEqual(code, 0)
        code, out = await self.run_cli("list")
        self.assertEqual(out, [
            "large-status\tsensor.villa_kuppis_large_target\tsensor\tlarge\t",
            "large-temperature\tsensor.alpstuga\tsensor\tlarge\tALPSTUGA"])
        store = BindingStore(Path(self.tmp.name) / "data" / cottage.SITES_DB)
        self.assertEqual(store.bindings("cottage")[1].room, "large")
        store.close()
        code, out = await self.run_cli("unbind", "cottage", "large-temperature")
        self.assertEqual((code, out), (0, ["unbound large-temperature"]))
        code, out = await self.run_cli("unbind", "cottage", "large-temperature")
        self.assertEqual(code, 1)
        _, out = await self.run_cli("list")
        self.assertEqual(len(out), 1)

    async def test_bad_bindings_are_refused(self):
        for argv in (
                ("bind", "cottage", "x", "light.kitchen", "sensor", "--room", "large"),
                ("bind", "home", "x", "sensor.a", "sensor", "--room", "large"),
                # The room status sensor needs its own room.
                ("bind", "cottage", "x", "sensor.villa_kuppis_small_target", "sensor",
                 "--room", "large"),
                ("bind", "cottage", "x", "sensor.villa_kuppis_small_target", "sensor")):
            code, out = await self.run_cli(*argv)
            self.assertEqual(code, 1, argv)
            self.assertTrue(out[0].startswith("error:"), out)

    async def test_discover_lists_bindable_entities_with_their_binding(self):
        self.ha.set_state("sensor.alpstuga", "5.0", {"friendly_name": "ALPSTUGA"}, push=False)
        self.ha.set_state("climate.mill_1", "heat", climate_attrs(friendly_name="Mill 1"),
                          push=False)
        self.ha.set_state("light.kitchen", "on", {}, push=False)
        await self.run_cli("bind", "cottage", "large-temperature", "sensor.alpstuga",
                           "sensor", "--room", "large")
        code, out = await self.run_cli("discover")
        self.assertEqual(code, 0, out)
        self.assertEqual(out, ["climate.mill_1\tclimate\tMill 1\t-",
                               "sensor.alpstuga\tsensor\tALPSTUGA\tlarge-temperature"])

    async def test_discover_reports_a_refused_token_without_printing_it(self):
        self.ha.token = "another-token"
        code, out = await self.run_cli("discover")
        self.assertEqual(code, 1)
        self.assertIn("refused", out[0])
        self.assertNotIn(TOKEN, " ".join(out))


class StoreTests(unittest.TestCase):
    def test_records_on_change_or_after_the_interval_only(self):
        with tempfile.TemporaryDirectory() as temp:
            store = CottageStore(Path(temp) / "pumps.db")
            t = 1_700_000_000
            self.assertEqual(store.record("cottage", "d", t, value=5.0), (True, None))
            self.assertEqual(store.record("cottage", "d", t + 60, value=5.0), (False, None))
            self.assertEqual(store.record("cottage", "d", t + 61, value=5.5)[0], True)
            self.assertEqual(store.record("cottage", "d", t + 61 + HISTORY_INTERVAL_SECONDS - 1,
                                          value=5.5)[0], False)
            self.assertEqual(store.record("cottage", "d", t + 61 + HISTORY_INTERVAL_SECONDS,
                                          value=5.5)[0], True)
            rows = store.series("cottage", "d", "value",
                                datetime.fromtimestamp(t - 10, timezone.utc),
                                datetime.fromtimestamp(t + 1000, timezone.utc), 288)
            self.assertEqual([r[1] for r in rows], [5.0, 5.5, 5.5])
            store.close()


def bind_all(store):
    # The large ALPSTUGA is recognised by its device class, not its name.
    for device_id, entity_id, kind, room, label in (
            ("large-alpstuga", "sensor.large_alpstuga_t", "sensor", "large", "ALPSTUGA"),
            ("large-humidity", "sensor.large_alpstuga_h", "sensor", "large", "Kosteus"),
            ("large-heater-1", "climate.large_mill_1", "climate", "large", "Mill 1"),
            ("large-status", "sensor.villa_kuppis_large_target", "sensor", "large", "Tila"),
            ("small-temperature", "sensor.small_alpstuga_t", "sensor", "small", "ALPSTUGA"),
            ("small-status", "sensor.villa_kuppis_small_target", "sensor", "small", "Tila")):
        store.bind("cottage", device_id, entity_id, kind, label, room)


def status_attrs(mode, reason="", **extra):
    return {"mode": mode, "reason": reason, "drive": None, "room_temperature": None,
            "requested_target": 19.0, "revision": 7, "valid_until": None,
            "manual_until": None, "manual_by": None, **extra}


class ViewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.ha = FakeHA(self.clock)
        await self.ha.start()
        temperature = {"unit_of_measurement": "°C", "device_class": "temperature"}
        self.ha.set_state("sensor.large_alpstuga_t", "18.4", temperature, push=False)
        self.ha.set_state("sensor.large_alpstuga_h", "41",
                          {"unit_of_measurement": "%", "device_class": "humidity"}, push=False)
        self.ha.set_state("climate.large_mill_1", "heat",
                          climate_attrs(friendly_name="Mill 1", temperature=21.0,
                                        hvac_modes=["off", "heat"]), push=False)
        self.ha.set_state("sensor.villa_kuppis_large_target", "19.0",
                          status_attrs("follow", "room 18.4 °C, target 19 °C"), push=False)
        self.ha.set_state("sensor.small_alpstuga_t", "7.2", temperature, push=False)
        self.ha.set_state("sensor.villa_kuppis_small_target", "7.0",
                          status_attrs("fallback", "no target from Villa Kuppis"), push=False)
        self.store = BindingStore(Path(self.tmp.name) / cottage.SITES_DB)
        bind_all(self.store)
        self.gateway = HomeAssistantGateway(
            "cottage", self.ha.url, lambda: TOKEN, self.store, now=self.clock,
            command_timeout=0.5, retry_base=0.05, retry_max=0.2, resync_interval=10.0)
        self.task = asyncio.create_task(self.gateway.run())
        await eventually(lambda: self.gateway.health()["state"] == "ok")
        self.site = CottageSite(self.tmp.name, self.gateway,
                                lambda: self.store.bindings("cottage"), now=self.clock)

    async def asyncTearDown(self):
        self.gateway.request_stop()
        await asyncio.wait_for(self.task, 3.0)
        await self.ha.stop()
        self.site._store.close()
        self.store.close()
        self.tmp.cleanup()

    def room(self, name="large"):
        return next(r for r in self.site.view()["rooms"] if r["id"] == name)

    def history(self, name="large"):
        now = self.clock()
        return self.site.history(name, now - timedelta(days=1), now + timedelta(hours=1), 288)

    async def push(self, entity_id, state, attrs=None):
        self.ha.set_state(entity_id, state, attrs, at=self.clock())
        key = (state, self.clock().isoformat())
        await eventually(lambda: (self.gateway._raw[entity_id]["state"],
                                  self.gateway._raw[entity_id]["last_updated"]) == key)

    async def test_live_view_shows_room_readings_and_status(self):
        large = self.room("large")
        self.assertEqual(large["state"], "ok")
        self.assertEqual(large["temperature"]["value"], 18.4)
        self.assertEqual(large["temperature"]["unit"], "°C")
        self.assertEqual(large["humidity"]["value"], 41.0)
        self.assertIsNone(large["co2"])
        heater = large["heaters"][0]
        self.assertEqual((heater["setpoint"], heater["mode"], heater["available"],
                          heater["status"]), (21.0, "heat", True, "ok"))
        status = large["status"]
        self.assertEqual((status["mode"], status["target"], status["revision"],
                          status["reason"], status["status"]),
                         ("follow", 19.0, 7, "room 18.4 °C, target 19 °C", "ok"))
        self.assertIsNotNone(status["source_ts"])
        small = self.room("small")
        self.assertEqual((small["status"]["mode"], small["status"]["target"]), ("fallback", 7.0))
        self.assertEqual(small["heaters"], [])
        health = self.site.view()["site"]
        self.assertEqual((health["connection"], health["state"], health["missing_bindings"]),
                         ("ok", "ok", []))

    async def test_live_to_stale_to_unavailable_to_missing(self):
        # Live: first samples record every fresh reading.
        self.assertEqual(self.site.sample(), 6)   # 6 bound devices with a room
        self.assertEqual(self.site.sample(), 0)       # nothing new: nothing repeated

        # A new temperature is a change.
        self.clock.advance(400)
        await self.push("sensor.large_alpstuga_t", "18.6")
        self.assertEqual(self.site.sample(), 1)

        # Stale: no reports for longer than the entity limit.
        self.clock.advance(2000)
        large = self.room("large")
        self.assertEqual(large["state"], "stale")
        self.assertEqual(large["temperature"]["status"], "stale")
        self.assertEqual(large["temperature"]["value"], 18.6)   # last value, marked stale
        self.assertEqual(self.site.sample(), 0)
        self.assertEqual(self.site.view()["site"]["connection"], "stale")
        resumed_at = self.clock()

        # Back to live.
        await self.push("sensor.large_alpstuga_t", "18.9")
        self.assertEqual(self.room("large")["temperature"]["status"], "ok")
        self.assertEqual(self.site.sample(), 1)

        # Unavailable: no value, no row.
        self.clock.advance(60)
        await self.push("sensor.large_alpstuga_t", "unavailable")
        temperature = self.room("large")["temperature"]
        self.assertEqual((temperature["status"], temperature["value"]), ("unavailable", None))
        self.assertEqual(self.room("large")["state"], "unavailable")
        self.assertEqual(self.site.sample(), 0)

        # Missing: the entity disappeared (renamed or removed in Home Assistant).
        self.ha.remove("sensor.small_alpstuga_t")
        await eventually(lambda: "small-temperature" in self.gateway.health()["missing"])
        small = self.room("small")
        self.assertEqual((small["temperature"]["status"], small["state"]), ("missing", "missing"))
        self.assertIn("small-temperature", self.site.view()["site"]["missing_bindings"])

        # History keeps the gap: values before and after the stale period only.
        points = self.history("large")["room_temp"]
        self.assertEqual([v for _, v in points], [18.4, 18.6, 18.9])
        times = [datetime.fromisoformat(t) for t, _ in points]
        self.assertLess(times[1], resumed_at - timedelta(seconds=1500))
        self.assertGreater(times[2] - times[1], timedelta(seconds=1500))

    async def test_heater_and_target_history(self):
        self.site.sample()
        self.clock.advance(400)
        await self.push("climate.large_mill_1", "heat",
                        climate_attrs(friendly_name="Mill 1", temperature=24.0,
                                      hvac_modes=["off", "heat"]))
        await self.push("sensor.villa_kuppis_large_target", "21.0",
                        status_attrs("follow", "room 18.4 °C, target 21 °C"))
        self.assertEqual(self.site.sample(), 2)
        history = self.history("large")
        self.assertEqual([v for _, v in history["plan"]], [19.0, 21.0])
        (heater,) = history["extra"]
        self.assertEqual(heater["key"], "heater:large-heater-1")
        self.assertEqual([v for _, v in heater["points"]], [21.0, 24.0])
        self.assertEqual([v for _, v in history["room_temp"]], [18.4])
        self.assertEqual(self.history("small")["room_temp"][0][1], 7.2)
        with self.assertRaises(ValueError):
            self.site.history("garage", self.clock(), self.clock(), 10)

    async def test_mode_changes_become_events_with_the_right_actor(self):
        self.site.sample()
        self.assertEqual(self.site.events(), [])      # the first reading is no change
        entity = "sensor.villa_kuppis_large_target"
        self.clock.advance(60)
        await self.push(entity, "7.0", status_attrs("fallback", "the target expired"))
        self.site.sample()
        self.clock.advance(60)
        await self.push(entity, "unknown", status_attrs(
            "manual", "climate.large_mill_1 changed by hand", manual_by="climate.large_mill_1",
            manual_until=(self.clock() + timedelta(hours=4)).isoformat()))
        self.site.sample()
        self.clock.advance(60)
        await self.push(entity, "19.0", status_attrs("thermostat", "room sensor stale"))
        self.site.sample()
        self.clock.advance(60)
        await self.push(entity, "19.0", status_attrs("follow", "room 18.4 °C, target 19 °C"))
        self.site.sample()
        # The same mode again (another reading) adds nothing.
        self.clock.advance(60)
        await self.push(entity, "19.5", status_attrs("follow", "room 18.4 °C, target 19.5 °C"))
        self.site.sample()

        events = list(reversed(self.site.events()))
        self.assertEqual([(e["requested"], e["actor"]) for e in events], [
            ("fallback", ACTOR_CONTROLLER), ("manual", ACTOR_MANUAL),
            ("thermostat", ACTOR_CONTROLLER), ("follow", ACTOR_CONTROLLER)])
        self.assertIn("climate.large_mill_1", events[1]["detail"])
        self.assertEqual(events[0]["detail"], "the target expired")
        self.assertEqual((events[0]["site"], events[0]["unit"], events[0]["unit_name"],
                          events[0]["result"]),
                         ("cottage", "cottage:large", "Mökki: Iso huone", "observed"))
        # The small room did not change mode.
        self.assertTrue(all(e["unit"] == "cottage:large" for e in events))

    async def test_a_refused_token_shows_as_a_rejected_credential(self):
        self.ha.token = "rotated"
        await self.ha.drop()
        await eventually(lambda: self.gateway.health()["state"] == "auth_failed")
        health = self.site.view()["site"]
        self.assertEqual(health["connection"], "auth_failed")
        self.assertEqual(self.room("large")["temperature"]["status"], "stale")
        self.assertEqual(self.site.sample(), 0)


class ServiceHttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = [datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)]
        self.closers = []

    def tearDown(self):
        for close in reversed(self.closers):
            try:
                close()
            except Exception:
                pass
        self.tmp.cleanup()

    def serve(self, service):
        service.start()
        self.closers.append(service.close)
        server = PumpAppServer(service, host="127.0.0.1", port=0)
        thread = threading.Thread(target=server.serve_forever,
                                  kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        self.closers.append(lambda: (server.shutdown(), server.server_close()))
        return server

    def get(self, server, path):
        url = f"http://127.0.0.1:{server.server_address[1]}{path}"
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def make_service(self, with_cottage=True):
        now = lambda: self.clock[0]
        def factory(data):
            gateway = FakeSiteGateway(now=now)
            return CottageSite(data, gateway, gateway.bindings, now=now,
                               sample_interval=3600)
        return PumpService(
            self.tmp.name, client=FakeClient([_unit_payload("u1", "Yksikkö", mode="Cool")]),
            now=now, poll_interval=0.05, stale_after=60, readback_delay=0.01,
            cottage_factory=factory if with_cottage else None)

    def test_cottage_sites_and_history_endpoints(self):
        service = self.make_service()
        server = self.serve(service)
        status, body = self.get(server, "/api/cottage")
        self.assertEqual(status, 200)
        self.assertTrue(body["configured"])
        rooms = {r["id"]: r for r in body["rooms"]}
        self.assertEqual(set(rooms), {"large", "small"})
        self.assertEqual(rooms["large"]["status"]["mode"], "follow")
        self.assertEqual(rooms["small"]["status"]["mode"], "fallback")
        self.assertEqual(rooms["large"]["name"], "Iso huone")
        self.assertEqual(len(rooms["large"]["heaters"]), 3)
        self.assertTrue(all(r["state"] == "ok" for r in rooms.values()))
        status, sites_body = self.get(server, "/api/sites")
        self.assertEqual(sites_body[0]["site_id"], "cottage")
        self.assertEqual(sites_body[0]["connection"], "ok")

        # Record twenty minutes of the fake cottage, then graph the room.
        for _ in range(5):
            service.cottage.sample()
            self.clock[0] += timedelta(seconds=HISTORY_INTERVAL_SECONDS)
        service.store.outdoor_upsert("cottage", [(self.clock[0] - timedelta(hours=1), -3.5)])
        status, hist = self.get(server, "/api/history?unit=cottage:large&range=day")
        self.assertEqual(status, 200)
        self.assertEqual(hist["unit"], "cottage:large")
        self.assertEqual(len(hist["room_temp"]), 5)
        self.assertEqual({v for _, v in hist["plan"]}, {19.0})
        self.assertEqual(len(hist["extra"]), 3)
        self.assertEqual(hist["outdoor"][0][1], -3.5)       # the cottage's outdoor site
        self.assertEqual(hist["setpoint"], [])
        self.assertIsNone(hist["price"])
        self.assertEqual(hist["labels"]["plan"], "Voimassa oleva tavoite")
        status, hist = self.get(server, "/api/history?unit=cottage:garage")
        self.assertEqual(status, 400)
        # Pumps still graph as before.
        status, hist = self.get(server, "/api/history?unit=u1&range=day")
        self.assertEqual((status, hist["extra"] if "extra" in hist else []), (200, []))

        # Wrong method on the new paths.
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_address[1]}/api/cottage", data=b"{}",
            method="POST")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(caught.exception.code, 405)

    def test_events_merge_pump_and_cottage_by_time(self):
        service = self.make_service()
        server = self.serve(service)
        service.store.add_event("2026-10-20T11:00:00+00:00", "u1", "Yksikkö", "web",
                                "setpoint", 22.0)
        service.cottage._store.add_event(
            int(datetime(2026, 10, 20, 11, 30, tzinfo=timezone.utc).timestamp()),
            "cottage", "small", ACTOR_CONTROLLER, "mode", "follow", "fallback", "expired")
        service.cottage._store.add_event(
            int(datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc).timestamp()),
            "cottage", "large", ACTOR_MANUAL, "mode", "follow", "manual", "by hand")
        status, events = self.get(server, "/api/events")
        self.assertEqual(status, 200)
        self.assertEqual([(e["site"], e["actor"]) for e in events],
                         [("cottage", ACTOR_CONTROLLER), ("home", "web"),
                          ("cottage", ACTOR_MANUAL)])
        status, events = self.get(server, "/api/events?limit=1")
        self.assertEqual(len(events), 1)

    def post(self, server, path, body):
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_address[1]}{path}",
            data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def test_each_heater_switches_off_and_back_on(self):
        service = self.make_service()
        server = self.serve(service)
        _, body = self.get(server, "/api/cottage")
        heaters = {h["device_id"]: h for r in body["rooms"] for h in r["heaters"]}
        self.assertTrue(all(h["switchable"] and h["enabled"] and h["switch"] is None
                            for h in heaters.values()))
        status, cmd = self.post(server, "/api/cottage/heater",
                                {"device_id": "large-heater-2", "enabled": False})
        self.assertEqual((status, cmd["state"]), (200, "confirmed"))
        self.assertEqual(cmd["action"], "villa_kuppis_cottage.set_heater_enabled")
        self.assertEqual(cmd["device_id"], "large-status")
        self.assertEqual(cmd["value"], {"entity_id": "climate.large_mill_2", "enabled": False})
        _, body = self.get(server, "/api/cottage")
        heaters = {h["device_id"]: h for r in body["rooms"] for h in r["heaters"]}
        self.assertFalse(heaters["large-heater-2"]["enabled"])
        self.assertEqual(heaters["large-heater-2"]["mode"], "off")
        self.assertEqual(heaters["large-heater-2"]["switch"]["state"], "confirmed")
        self.assertTrue(heaters["large-heater-1"]["enabled"])
        _, events = self.get(server, "/api/events")
        self.assertEqual((events[0]["action"], events[0]["requested"], events[0]["detail"]),
                         ("heater", "off", "Mill 1050 W (2)"))
        self.post(server, "/api/cottage/heater", {"device_id": "large-heater-2", "enabled": True})
        _, body = self.get(server, "/api/cottage")
        self.assertTrue(all(h["enabled"] for r in body["rooms"] for h in r["heaters"]))

        for bad in ({"device_id": "large-status", "enabled": False},
                    {"device_id": "large-heater-9", "enabled": False},
                    {"device_id": "large-heater-1", "enabled": "no"}):
            status, error = self.post(server, "/api/cottage/heater", bad)
            self.assertEqual(status, 400, bad)
            self.assertTrue(error["detail"])

    def test_a_room_can_read_another_sensor_and_go_back(self):
        server = self.serve(self.make_service())
        status, cmd = self.post(server, "/api/cottage/room_sensor",
                                {"room": "small", "entity_id": "sensor.ikkuna"})
        self.assertEqual((status, cmd["state"]), (200, "confirmed"))
        self.assertEqual(cmd["value"], {"room": "small", "entity_id": "sensor.ikkuna"})
        _, body = self.get(server, "/api/cottage")
        rooms = {r["id"]: r for r in body["rooms"]}
        self.assertEqual(rooms["small"]["status"]["chosen_sensor"], "sensor.ikkuna")
        self.assertIsNone(rooms["large"]["status"]["chosen_sensor"])
        _, events = self.get(server, "/api/events")
        self.assertEqual((events[0]["action"], events[0]["requested"]), ("room_sensor", "sensor.ikkuna"))
        self.post(server, "/api/cottage/room_sensor", {"room": "small", "entity_id": None})
        _, body = self.get(server, "/api/cottage")
        self.assertIsNone(body["rooms"][1]["status"]["chosen_sensor"])
        self.assertEqual(self.post(server, "/api/cottage/room_sensor", {"room": "sauna"})[0], 400)

    def test_heaters_without_integration_support_have_no_switch(self):
        service = self.make_service()
        server = self.serve(service)
        gateway = service.cottage._gateway
        original = gateway.snapshot

        def old_integration():
            out = original()
            for state in out.values():
                (state.get("attributes") or {}).pop("disabled_heaters", None)
            return out
        gateway.snapshot = old_integration
        _, body = self.get(server, "/api/cottage")
        self.assertFalse(any(h["switchable"] for r in body["rooms"] for h in r["heaters"]))
        status, error = self.post(server, "/api/cottage/heater",
                                  {"device_id": "large-heater-1", "enabled": False})
        self.assertEqual(status, 400)
        self.assertIn("install.sh", error["detail"])

    def test_without_a_cottage_the_paths_are_empty_and_history_is_refused(self):
        server = self.serve(self.make_service(with_cottage=False))
        self.assertEqual(self.get(server, "/api/cottage"),
                         (200, {"configured": False, "site": None, "rooms": []}))
        self.assertEqual(self.get(server, "/api/sites"), (200, []))
        self.assertEqual(self.get(server, "/api/history?unit=cottage:large")[0], 400)

    def test_close_stops_the_cottage_gateway(self):
        service = self.make_service()
        service.start()
        self.assertEqual(service.cottage._gateway.health()["state"], "ok")
        service.close()
        self.assertEqual(service.cottage._gateway.health()["state"], "stopped")


class DemoTests(unittest.TestCase):
    def test_demo_shows_a_cottage_without_network(self):
        with tempfile.TemporaryDirectory() as temp:
            service = make_demo_service(temp)
            try:
                service.start()
                view = service.cottage_view()
                modes = {r["id"]: r["status"]["mode"] for r in view["rooms"]}
                self.assertEqual(modes, {"large": "follow", "small": "fallback"})
                self.assertTrue(all(r["state"] == "ok" and r["temperature"]["value"] is not None
                                    for r in view["rooms"]))
                self.assertEqual(view["site"]["connection"], "ok")
                service.cottage.sample()
            finally:
                service.close()

    def test_page_has_the_cottage_section_in_finnish(self):
        page = render_page(demo=True)
        for text in ("Mökki", "Yhteys mökkiin: ", "Seuraa anturia", "Patterien termostaatti",
                     "Turvalämpö", "Käsiohjaus", "ei yhteyttä", "tunnus hylätty",
                     "vanhentunut", "/api/cottage"):
            self.assertIn(text, page)
        self.assertNotIn("/*COTTAGE", page)
        self.assertNotIn("<!--COTTAGE", page)


class RoleTests(unittest.TestCase):
    def test_roles_come_from_entity_name_device_id_or_device_class(self):
        def b(device_id, entity_id, kind="sensor", room="large"):
            return Binding("cottage", device_id, entity_id, kind, "", room)
        self.assertEqual(role_of(b("x", "climate.m", "climate")), "heater")
        self.assertEqual(role_of(b("x", "sensor.villa_kuppis_large_target")), "status")
        self.assertEqual(role_of(b("x", "sensor.villa_kuppis_small_target")), None)
        self.assertEqual(role_of(b("large-co2", "sensor.a")), "co2")
        self.assertEqual(role_of(b("large-pm25", "sensor.a")), "pm25")
        self.assertEqual(role_of(b("large-humidity", "sensor.a")), "humidity")
        self.assertEqual(role_of(b("large-temperature", "sensor.a")), "temperature")
        self.assertEqual(role_of(b("alpstuga", "sensor.a"), {"caps": {"device_class": "carbon_dioxide"}}),
                         "co2")
        self.assertIsNone(role_of(b("alpstuga", "sensor.a")))


if __name__ == "__main__":
    unittest.main()
