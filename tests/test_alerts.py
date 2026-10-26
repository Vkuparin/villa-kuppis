"""Phone alerts (alerts.py): fake sites and clock, and a fake Home Assistant on localhost."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from villa_kuppis.alerts import AlertMonitor, HomeAssistantNotifier
from villa_kuppis.ha_gateway import HAError
from villa_kuppis.ha_pumps import SessionReloader
from villa_kuppis.settings import SettingsStore

from tests.test_ha_gateway import GatewayTestCase

MINUTE = timedelta(minutes=1)


class FakeCottage:
    def __init__(self):
        self.connection = "ok"
        self.temps = {"large": 6.0, "small": 6.0}

    def view(self):
        names = {"large": "Iso huone", "small": "Pieni huone"}
        return {"site": {"connection": self.connection},
                "rooms": [{"id": r, "name": names[r],
                           "temperature": None if t is None else {"value": t}}
                          for r, t in self.temps.items()]}


class FakeService:
    def __init__(self):
        self.units = []
        self.cottage = FakeCottage()

    def view(self):
        return {"units": self.units}


class FakeNotifier:
    def __init__(self):
        self.sent = []
        self.ok = True

    def send(self, title, message):
        self.sent.append((title, message))
        return self.ok


def unit(unit_id="u1", name="Olohuone", **extra):
    values = {"id": unit_id, "name": name, "power": True, "mode": "Cool", "in_error": False}
    values.update(extra)
    return values


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = SettingsStore(Path(self.tmp.name) / "pumps.db")
        self.service = FakeService()
        self.notifier = FakeNotifier()
        self.now = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)
        self.monitor = AlertMonitor(self.service, self.notifier, self.settings,
                                    now=lambda: self.now)

    def tearDown(self):
        self.tmp.cleanup()

    def check(self, minutes=0):
        self.now += minutes * MINUTE
        self.monitor.check()
        return [title for title, _ in self.notifier.sent]

    def test_cold_room_alerts_once_and_is_over_a_degree_above_the_limit(self):
        self.assertEqual(self.check(), [])
        self.service.cottage.temps["large"] = 2.5
        self.assertEqual(self.check(1), ["Mökki: Iso huone on kylmä"])
        self.assertEqual(self.notifier.sent[0][1], "Lämpötila on 2,5 °C, alle hälytysrajan 3 °C.")
        self.service.cottage.temps["large"] = 3.5
        self.assertEqual(len(self.check(1)), 1)
        self.service.cottage.temps["large"] = 4.0
        self.assertEqual(self.check(1)[1:], ["Mökki: Iso huone on taas lämpimämpi"])
        self.assertEqual(len(self.check(1)), 2)

    def test_cold_limit_comes_from_the_settings(self):
        self.settings.set("site:cottage", "cold_alert_c", 5.0, "test")
        self.service.cottage.temps["small"] = 4.5
        self.assertEqual(self.check(), ["Mökki: Pieni huone on kylmä"])

    def test_unreachable_cottage_waits_30_minutes_and_keeps_a_cold_room_open(self):
        self.service.cottage.temps["large"] = 2.0
        self.check()
        self.service.cottage.connection = "offline"
        self.assertEqual(self.check(1), ["Mökki: Iso huone on kylmä"])
        self.assertEqual(len(self.check(29)), 1)
        self.assertEqual(self.check(1)[1:], ["Mökki ei vastaa"])
        self.service.cottage.connection = "ok"
        self.assertEqual(self.check(1)[2:], ["Mökki vastaa taas"])
        self.service.cottage.temps["large"] = None   # sensor gone: still open
        self.assertEqual(len(self.check(1)), 3)

    def test_pump_problem_is_reported_after_30_minutes(self):
        self.service.units = [unit(power=None, mode=None, in_error=True)]
        self.check()
        self.assertEqual(self.check(29), [])
        self.assertEqual(self.check(1), ["Koti: pumppu Olohuone ei toimi"])
        self.assertIn("ei ole ollut käytettävissä", self.notifier.sent[0][1])
        self.service.units = [unit()]
        self.assertEqual(self.check(1)[1:], ["Koti: pumppu Olohuone toimii taas"])

    def test_short_pump_blip_and_device_error(self):
        self.service.units = [unit(in_error=True)]
        self.check()
        self.service.units = [unit()]
        self.check(10)
        self.service.units = [unit(in_error=True)]
        self.assertEqual(self.check(1), [])
        self.assertEqual(self.check(30), ["Koti: pumppu Olohuone ei toimi"])
        self.assertIn("laitevirheestä", self.notifier.sent[0][1])

    def test_each_alert_has_its_own_switch(self):
        self.settings.set("site:cottage", "alert_cold", False, "test")
        self.settings.set("room:home/u1", "alert_pump", False, "test")
        self.service.cottage.temps["large"] = 1.0
        self.service.units = [unit(in_error=True), unit("u2", "Makuuhuone", in_error=True)]
        self.assertEqual(self.check(), [])
        self.service.cottage.connection = "offline"
        self.check(1)
        self.assertEqual(sorted(self.check(30)),
                         ["Koti: pumppu Makuuhuone ei toimi", "Mökki ei vastaa"])
        # Switched off while open: no notice when it is over either.
        self.settings.set("site:cottage", "alert_offline", False, "test")
        self.service.cottage.connection = "ok"
        self.service.cottage.temps["large"] = 6.0
        self.assertEqual(len(self.check(1)), 2)

    def test_notice_that_was_not_sent_is_retried(self):
        self.notifier.ok = False
        self.service.cottage.temps["large"] = 1.0
        self.check()
        self.check(1)
        self.notifier.ok = True
        self.check(1)
        self.check(1)
        self.assertEqual(len(self.notifier.sent), 3)
        self.notifier.ok = False
        self.service.cottage.temps["large"] = 6.0
        self.check(1)
        self.notifier.ok = True
        self.check(1)
        self.check(1)
        self.assertEqual([t for t, _ in self.notifier.sent[3:]],
                         ["Mökki: Iso huone on taas lämpimämpi"] * 2)


class FakeGateway:
    def __init__(self):
        self.state = "ok"
        self.calls = []
        self.error = None

    def health(self):
        return {"state": self.state}

    def snapshot(self):
        return {"u1": {"entity_id": "climate.olohuone"}, "u2": {"entity_id": "climate.makuuhuone"}}

    def call(self, domain, service, data, entity_id=None):
        self.calls.append((domain, service, entity_id))
        if self.error:
            raise self.error


class ReloaderTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)
        self.gateway = FakeGateway()
        self.reloader = SessionReloader(self.gateway, now=lambda: self.now)
        self.down = [unit(power=None, mode=None, in_error=True),
                     unit("u2", "Makuuhuone", power=None, mode=None, in_error=True)]

    def check(self, minutes, units=None, enabled=True):
        self.now += minutes * MINUTE
        self.reloader.check(self.down if units is None else units, enabled)
        return len(self.gateway.calls)

    def test_reloads_after_ten_minutes_and_at_most_hourly(self):
        self.assertEqual(self.check(0), 0)
        self.assertEqual(self.check(9), 0)
        with self.assertLogs("villa_kuppis.ha_pumps", "INFO"):
            self.assertEqual(self.check(1), 1)
        self.assertEqual(self.gateway.calls[0],
                         ("homeassistant", "reload_config_entry", "climate.olohuone"))
        self.assertTrue(self.reloader.tried)
        self.assertEqual(self.check(59), 1)
        self.assertEqual(self.check(1), 2)
        self.check(1, [unit(), unit("u2", "Makuuhuone")])
        self.assertFalse(self.reloader.tried)

    def test_no_reload_for_one_pump_when_off_or_when_home_assistant_is_down(self):
        self.check(0, [self.down[0], unit("u2", "Makuuhuone")])
        self.assertEqual(self.check(15, [self.down[0], unit("u2", "Makuuhuone")]), 0)
        self.assertEqual(self.check(15, enabled=False), 0)
        self.gateway.state = "disconnected"
        self.assertEqual(self.check(15), 0)
        self.gateway.state = "ok"
        self.assertEqual(self.check(1), 1)

    def test_refused_reload_says_why(self):
        self.gateway.error = HAError("unauthorized", "Unauthorized")
        self.check(0)
        with self.assertLogs("villa_kuppis.ha_pumps", "WARNING") as logs:
            self.check(10)
        self.assertIn("administrator", logs.output[0])

    def test_pump_alert_says_when_the_reload_did_not_help(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        service, notifier = FakeService(), FakeNotifier()
        service.cottage = None
        service.units = self.down
        monitor = AlertMonitor(service, notifier, SettingsStore(Path(tmp.name) / "pumps.db"),
                               reloader=self.reloader, now=lambda: self.now)
        for _ in range(31):
            monitor.check()
            self.now += MINUTE
        self.assertEqual(len(self.gateway.calls), 1)
        self.assertEqual(len(notifier.sent), 2)
        self.assertIn("Uusi kirjautuminen MELCloud Homeen ei auttanut", notifier.sent[0][1])


class NotifierTests(GatewayTestCase):
    async def send(self, notifier):
        return await asyncio.get_running_loop().run_in_executor(
            None, notifier.send, "Mökki ei vastaa", "Viesti")

    async def test_sends_to_every_phone_with_the_app(self):
        self.ha.services["notify"] = {"mobile_app_ville": {}, "mobile_app_tabletti": {},
                                      "persistent_notification": {}}
        notifier = HomeAssistantNotifier(await self.start_gateway())
        self.assertTrue(await self.send(notifier))
        notices = sorted(c for c in self.ha.calls if c[0] == "notify")
        self.assertEqual(notices, [
            ("notify", "mobile_app_tabletti", None, {"title": "Mökki ei vastaa", "message": "Viesti"}),
            ("notify", "mobile_app_ville", None, {"title": "Mökki ei vastaa", "message": "Viesti"})])

    async def test_one_failing_phone_does_not_stop_the_others(self):
        self.ha.services["notify"] = {"mobile_app_ville": {}, "mobile_app_tabletti": {}}
        self.ha.fail["mobile_app_ville"] = ("service_not_found", "gone")
        notifier = HomeAssistantNotifier(await self.start_gateway())
        with self.assertLogs("villa_kuppis.alerts", "WARNING"):
            self.assertTrue(await self.send(notifier))

    async def test_reload_targets_a_pump_entity(self):
        later = [self.clock()]   # the gateway's own clock stays, so it stays fresh
        reloader = SessionReloader(await self.start_gateway(), now=lambda: later[0])
        down = [unit("pump-living", power=None, mode=None, in_error=True)]
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, reloader.check, down, True)
        later[0] += timedelta(seconds=600)
        with self.assertLogs("villa_kuppis.ha_pumps", "INFO"):
            await loop.run_in_executor(None, reloader.check, down, True)
        self.assertIn(("homeassistant", "reload_config_entry", "climate.olohuone", {}),
                      self.ha.calls)

    async def test_no_phone_or_no_connection_is_not_sent(self):
        notifier = HomeAssistantNotifier(self.make_gateway())
        with self.assertLogs("villa_kuppis.alerts", "WARNING"):
            self.assertFalse(await self.send(notifier))
        notifier = HomeAssistantNotifier(await self.start_gateway())
        with self.assertLogs("villa_kuppis.alerts", "WARNING"):
            self.assertFalse(await self.send(notifier))
        self.assertEqual([c for c in self.ha.calls if c[0] == "notify"], [])


if __name__ == "__main__":
    unittest.main()
