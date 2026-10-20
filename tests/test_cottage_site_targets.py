"""Price targets and visits wired into the cottage site (cards H2.4, F4.3, H2.5).

A fake cottage Home Assistant on localhost (FakeCottageHA) behind the real
gateway; the site's target tick is called by hand. The visit API runs on a
localhost HTTP server over the offline demo service.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import IsolatedAsyncioTestCase

from tests.common import elering_payload
from tests.test_cottage_targets import DAY, PATTERN, FakeCottageHA
from tests.test_ha_gateway import TOKEN, Clock, eventually
from villa_kuppis.cottage import CottageSite
from villa_kuppis.ha_gateway import BindingStore, HomeAssistantGateway
from villa_kuppis.prices import INTERVAL_SECONDS, helsinki_day_bounds, validate_day
from villa_kuppis.pump_app import PumpAppServer, make_demo_service, render_page
from villa_kuppis.storage import PriceStore


class SiteTargetTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        start, end = helsinki_day_bounds(DAY)
        count = (end - start) // INTERVAL_SECONDS
        intervals = validate_day(elering_payload(DAY, [PATTERN[i % 4] for i in range(count)]), DAY)
        with PriceStore(self.data / "prices.db") as store:
            store.replace_day(DAY.isoformat(), intervals, datetime.now(timezone.utc))
        self.clock = Clock()
        self.clock.now = datetime.fromtimestamp(start, timezone.utc) + timedelta(minutes=1)
        self.ha = FakeCottageHA(self.clock)
        await self.ha.start()
        for room in ("large", "small"):
            self.ha.set_state(f"sensor.villa_kuppis_{room}_target", "7.0",
                              {"mode": "fallback", "revision": None}, push=False)
        self.bindings = BindingStore(self.data / "sites.db")
        self.bindings.bind("cottage", "large-status", "sensor.villa_kuppis_large_target",
                           "sensor", "", "large")
        self.bindings.bind("cottage", "small-status", "sensor.villa_kuppis_small_target",
                           "sensor", "", "small")
        self.gateway = HomeAssistantGateway(
            "cottage", self.ha.url, lambda: TOKEN, self.bindings, now=self.clock,
            command_timeout=0.5, confirm_timeout=0.5, retry_base=0.05, retry_max=0.2,
            site_stale_after=1e9)
        self.task = asyncio.create_task(self.gateway.run())
        await eventually(lambda: self.gateway.health()["state"] == "ok")
        self.automation = True
        self.site = CottageSite(self.data, self.gateway, lambda: self.bindings.bindings("cottage"),
                                now=self.clock)
        self.site.configure_targets(prices_db=self.data / "prices.db", overrides=lambda: {},
                                    automation_on=lambda: self.automation)

    async def asyncTearDown(self):
        self.gateway.request_stop()
        await asyncio.wait_for(self.task, 3.0)
        await self.ha.stop()
        self.site._store.close()
        self.site._visits.close()
        self.bindings.close()
        self.tmp.cleanup()

    async def settle(self, sent):
        for cmd in sent:
            if cmd["state"] == "pending":
                await self.gateway.wait(cmd["request_id"], 3.0)

    def room(self, name):
        return {r["id"]: r for r in self.site.view()["rooms"]}[name]

    async def test_tick_sends_acknowledges_and_shows_the_plan(self):
        self.assertIsNone(self.room("large")["plan"])
        sent = self.site.tick_targets()
        self.assertEqual(len(sent), 2)
        await self.settle(sent)
        self.assertEqual({t["room"]: t["target"] for t in self.ha.targets},
                         {"large": 12.0, "small": 12.0})
        plan = self.room("large")["plan"]
        self.assertEqual((plan["target"], plan["reason"], plan["reason_text"]),
                         (12.0, "threshold", "Hintaraja"))
        self.assertEqual(plan["price"], 2.0)
        self.assertIsNotNone(plan["acked_at"])
        self.assertTrue(self.site.view()["automation_on"])
        # Confirmed writes never reach the event log.
        self.assertEqual(self.site.events(), [])

    async def test_automation_off_sends_nothing_but_shows_the_plan(self):
        self.automation = False
        self.assertEqual(self.site.tick_targets(), [])
        self.assertEqual(self.ha.calls, [])
        view = self.site.view()
        self.assertFalse(view["automation_on"])
        self.assertEqual(self.room("large")["plan"]["target"], 12.0)
        self.assertIsNone(self.room("large")["plan"]["acked_at"])

    async def test_room_without_a_status_binding_is_skipped(self):
        self.bindings.unbind("cottage", "small-status")
        with self.assertLogs(level="WARNING") as logs:
            sent = self.site.tick_targets()
        self.assertIn("small", logs.output[0])
        await self.settle(sent)
        self.assertEqual([t["room"] for t in self.ha.targets], ["large"])
        self.assertIsNone(self.room("small")["plan"])
        self.site.tick_targets()   # logged once only: no new warning needed

    async def test_unknown_write_is_logged_once_as_the_plan(self):
        self.ha.hang = True
        sent = self.site.tick_targets()
        await self.settle(sent)
        self.site.tick_targets()
        events = self.site.events()
        self.assertEqual({e["actor"] for e in events}, {"Suunnitelma"})
        self.assertEqual(len(events), 2)    # one per room
        self.assertEqual(events[0]["action"], "target")
        self.assertIn("ei voitu varmistaa", events[0]["detail"])
        self.clock.advance(61)
        self.ha.hang = False
        await self.settle(self.site.tick_targets())
        self.assertEqual(len(self.site.events()), 2)

    async def test_visit_start_replaces_targets_and_shows_in_the_plan(self):
        arrival = self.clock() + timedelta(hours=13)
        result = await asyncio.to_thread(
            self.site.start_visit, arrival.isoformat(), 21, ["large"], "Ville")
        self.assertEqual(result["visit"]["rooms"], ["large"])
        self.assertEqual(result["plan"]["reason"], "cheapest")
        await self.settle(self.site.tick_targets())
        self.assertEqual(self.room("large")["plan"]["reason"], "threshold")
        self.clock.advance(3600 * 5)
        await self.settle(self.site.tick_targets())
        self.assertEqual(self.room("large")["plan"]["reason"], "visit")
        self.assertEqual(self.room("large")["plan"]["reason_text"], "Vierailu")
        self.assertEqual(self.room("large")["plan"]["target"], 21.0)
        self.assertEqual(self.site.visit_view()["plan"]["reason"], "started")
        self.site.end_visit()
        self.assertIsNone(self.site.visit_view()["visit"])


class VisitApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.service = make_demo_service(self.tmp.name)
        self.service.start()
        self.server = PumpAppServer(self.service, host="127.0.0.1", port=0)
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05},
                         daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.service.close()
        self.tmp.cleanup()

    def call(self, path, body=None, method=None):
        url = f"http://127.0.0.1:{self.server.server_address[1]}{path}"
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            url, data=data, method=method or ("POST" if data is not None else "GET"),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def arrival(self, hours):
        return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()

    def test_visit_flow_in_the_demo(self):
        self.assertEqual(self.call("/api/visit"), (200, {"visit": None, "plan": None}))
        status, body = self.call("/api/visit", {"arrival": self.arrival(3), "target": 21,
                                                "rooms": ["large", "small"]})
        self.assertEqual(status, 200)
        self.assertEqual(body["visit"]["target"], 21.0)
        self.assertTrue(body["visit"]["created_by"])
        self.assertEqual(body["plan"]["reason"], "started")   # under 12 h away: heat now
        # The demo cottage acknowledges the visit target once the tick runs.
        self.service.cottage.tick_targets()
        status, cottage = self.call("/api/cottage")
        rooms = {r["id"]: r for r in cottage["rooms"]}
        self.assertEqual(rooms["small"]["plan"]["reason_text"], "Vierailu")
        self.assertEqual(rooms["small"]["status"]["requested_target"], 21.0)
        self.assertEqual(rooms["small"]["status"]["mode"], "follow")
        self.assertIsNotNone(rooms["small"]["plan"]["acked_at"])
        status, body = self.call("/api/visit")
        self.assertIsNotNone(body["visit"]["heat_start"])
        status, body = self.call("/api/visit/end", {})
        self.assertEqual((status, body["visit"]), (200, None))

    def test_invalid_input_gets_a_finnish_400(self):
        good = {"arrival": self.arrival(5), "target": 21, "rooms": ["large"]}
        for change, text in (
                ({"arrival": self.arrival(-1)}, "tulevaisuudessa"),
                ({"arrival": self.arrival(24 * 30)}, "14 päivän"),
                ({"arrival": "2026-11-01T10:00:00"}, "aikavyöhyke"),
                ({"arrival": "pian"}, "kelvollinen"),
                ({"arrival": None}, "puuttuu"),
                ({"target": 40}, "5–25"),
                ({"target": "kuuma"}, "luku"),
                ({"rooms": []}, "vähintään yksi"),
                ({"rooms": ["sauna"]}, "Tuntematon")):
            status, body = self.call("/api/visit", {**good, **change})
            self.assertEqual(status, 400, change)
            self.assertIn(text, body["detail"])
        self.assertIsNone(self.call("/api/visit")[1]["visit"])
        self.assertEqual(self.call("/api/visit/end", method="GET")[0], 405)

    def test_without_a_cottage_the_visit_paths_are_404(self):
        with tempfile.TemporaryDirectory() as temp:
            from villa_kuppis.pump_probe import FakeClient, _unit_payload
            from villa_kuppis.pump_service import PumpService
            service = PumpService(temp, client=FakeClient([_unit_payload("u1", "Y")]))
            server = PumpAppServer(service, host="127.0.0.1", port=0)
            threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05},
                             daemon=True).start()
            self.addCleanup(service.close)
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            base = f"http://127.0.0.1:{server.server_address[1]}"
            for path, data in (("/api/visit", None), ("/api/visit", b"{}"),
                               ("/api/visit/end", b"{}")):
                request = urllib.request.Request(
                    base + path, data=data, headers={"Content-Type": "application/json"})
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(request, timeout=5)
                self.assertEqual(caught.exception.code, 404)


class PageTests(unittest.TestCase):
    def test_page_has_the_visit_card_in_finnish(self):
        page = render_page(demo=True)
        for text in ("Aloita vierailu", "Lopeta vierailu", "Lämmitys alkaa", "Lämmitys alkoi",
                     "Saapuminen", "Tavoite saavutettu", "12 tuntia", "/api/visit",
                     "Suunniteltu tavoite", "Mökki kuitannut"):
            self.assertIn(text, page)


if __name__ == "__main__":
    unittest.main()
