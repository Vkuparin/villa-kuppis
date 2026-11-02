"""Phone alerts (alerts.py): fake sites and clock, and a fake Home Assistant on localhost."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from villa_kuppis.alerts import AlertMonitor, Delivery, HomeAssistantNotifier
from villa_kuppis.ha_gateway import HAError
from villa_kuppis.ha_pumps import EnergyRecorder, SessionReloader
from villa_kuppis.settings import SettingsStore

from tests.test_ha_gateway import GatewayTestCase

MINUTE = timedelta(minutes=1)


class FakeCottage:
    def __init__(self):
        self.connection = "ok"
        self.temps = {"large": 6.0, "small": 6.0}
        self.temp_status = {}             # room -> reading status other than ok
        self.heaters = {"large": [], "small": []}
        self.notices = []                 # due visit notices
        self.marked = set()
        self.air = {"large": {}, "small": {}}   # room -> {"humidity": %, "co2": ppm}
        self.visit = None

    def active_visit(self):
        return self.visit

    def visit_notices(self):
        return list(self.notices)

    def visit_notice_sent(self, visit_id, kind):
        return (visit_id, kind) in self.marked

    def mark_visit_notice(self, visit_id, kind):
        self.marked.add((visit_id, kind))

    def view(self):
        names = {"large": "Mökki", "small": "Kammari"}
        return {"site": {"connection": self.connection},
                "rooms": [{"id": r, "name": names[r],
                           "temperature": None if t is None else {
                               "value": t, "status": self.temp_status.get(r, "ok")},
                           "heaters": self.heaters[r],
                           **{role: {"value": v, "status": "ok"}
                              for role, v in self.air[r].items()}}
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

    def deliver(self, title, message):
        self.send(title, message)
        return Delivery(("ville",)) if self.ok else Delivery(reason="no_phones")


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

    def test_test_notice_goes_through_the_same_notifier(self):
        self.assertEqual(self.monitor.send_test("cottage").sent, ("ville",))
        title, message = self.notifier.sent[-1]
        self.assertEqual(title, "Villa Kuppis: testi-ilmoitus")
        self.assertIn("Mökki", message)
        self.notifier.ok = False
        self.assertEqual(self.monitor.send_test("home").reason, "no_phones")

    def test_cold_room_alerts_once_and_is_over_a_degree_above_the_limit(self):
        self.assertEqual(self.check(), [])
        self.service.cottage.temps["large"] = 2.5
        self.assertEqual(self.check(1), ["Mökki on kylmä"])
        self.assertEqual(self.notifier.sent[0][1], "Lämpötila on 2,5 °C, alle hälytysrajan 3 °C.")
        self.service.cottage.temps["large"] = 3.5
        self.assertEqual(len(self.check(1)), 1)
        self.service.cottage.temps["large"] = 4.0
        self.assertEqual(self.check(1)[1:], ["Mökki on taas lämpimämpi"])
        self.assertEqual(len(self.check(1)), 2)

    def test_cold_limit_comes_from_the_settings(self):
        self.settings.set("site:cottage", "cold_alert_c", 5.0, "test")
        self.service.cottage.temps["small"] = 4.5
        self.assertEqual(self.check(), ["Mökki: Kammari on kylmä"])

    def test_unreachable_cottage_waits_30_minutes_and_keeps_a_cold_room_open(self):
        self.service.cottage.temps["large"] = 2.0
        self.check()
        self.service.cottage.connection = "offline"
        self.assertEqual(self.check(1), ["Mökki on kylmä"])
        self.assertEqual(len(self.check(29)), 1)
        self.assertEqual(self.check(1)[1:], ["Mökki ei vastaa"])
        self.service.cottage.connection = "ok"
        self.assertEqual(self.check(1)[2:], ["Mökki vastaa taas"])
        self.service.cottage.temps["large"] = None   # sensor gone: still open
        self.assertEqual(len(self.check(1)), 3)

    def test_stale_reading_neither_raises_nor_clears_a_cold_alert(self):
        self.service.cottage.temp_status["large"] = "stale"
        self.service.cottage.temps["large"] = 1.0
        self.assertEqual(self.check(), [])
        self.service.cottage.temp_status.clear()
        self.assertEqual(self.check(1), ["Mökki on kylmä"])
        self.service.cottage.temp_status["large"] = "stale"
        self.service.cottage.temps["large"] = 9.0
        self.assertEqual(len(self.check(1)), 1)

    def test_missing_room_reading_alerts_after_an_hour(self):
        self.service.cottage.temp_status["small"] = "unavailable"
        self.check()
        self.assertEqual(self.check(59), [])
        self.assertEqual(self.check(1), ["Mökki: Kammari: lämpötila-anturi ei toimi"])
        self.service.cottage.temp_status.clear()
        self.assertEqual(self.check(1)[1:], ["Mökki: Kammari: lämpötila-anturi toimii taas"])

    def test_unavailable_heater_alerts_after_30_minutes_with_its_own_switch(self):
        heater = {"device_id": "small-heater-1", "label": "Kammari 1000W patteri",
                  "available": False, "status": "unavailable"}
        self.service.cottage.heaters["small"] = [heater]
        self.check()
        self.assertEqual(self.check(29), [])
        self.assertEqual(self.check(1), ["Mökki: Kammari: patteri Kammari 1000W patteri ei vastaa"])
        self.service.cottage.heaters["small"] = [{**heater, "available": True, "status": "ok"}]
        self.assertEqual(self.check(1)[1:],
                         ["Mökki: Kammari: patteri Kammari 1000W patteri vastaa taas"])
        self.settings.set("site:cottage", "alert_device", False, "test")
        self.service.cottage.heaters["small"] = [heater]
        self.assertEqual(len(self.check(40)), 2)

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

    def test_visit_notices_go_once_each_and_follow_their_switches(self):
        self.service.cottage.notices = [(1, "warm", "Mökki on lämmin", "Mökki 21 °C.")]
        self.assertEqual(self.check(), ["Mökki on lämmin"])
        self.assertEqual(self.check(1), ["Mökki on lämmin"])     # not again
        self.settings.set("site:cottage", "alert_visit_late", False, "test")
        self.service.cottage.notices = [(1, "late", "Mökki ei ehdi lämmetä ajoissa", "...")]
        self.assertEqual(len(self.check(1)), 1)
        self.assertIn((1, "late"), self.service.cottage.marked)   # switched off: dropped
        self.notifier.ok = False
        self.service.cottage.notices = [(2, "warm", "Mökki on lämmin", "")]
        self.check(1)
        self.assertNotIn((2, "warm"), self.service.cottage.marked)   # retried later
        self.notifier.ok = True
        self.check(1)
        self.assertIn((2, "warm"), self.service.cottage.marked)

    def test_damp_room_alerts_after_a_day_and_clears_five_points_under(self):
        self.service.cottage.air["large"]["humidity"] = 86.0
        self.assertEqual(self.check(), [])
        self.assertEqual(self.check(60 * 24 - 1), [])
        self.assertEqual(self.check(1), ["Mökki on kostea"])
        self.assertIn("yli 80 % (nyt 86 %)", self.notifier.sent[0][1])
        self.service.cottage.air["large"] = {}           # no reading: stays open
        self.assertEqual(len(self.check(1)), 1)
        self.service.cottage.air["large"]["humidity"] = 77.0
        self.assertEqual(len(self.check(1)), 1)
        self.service.cottage.air["large"]["humidity"] = 74.0
        self.assertEqual(self.check(1)[1:], ["Mökki: kosteus on laskenut"])

    def test_a_damp_spell_keeps_its_start_over_a_restart_and_a_blip(self):
        cottage = self.service.cottage
        cottage.air["large"]["humidity"] = 85.4
        # The stored readings show it damp since nearly a day (before a restart).
        cottage.above_since = lambda room, role, limit, lookback: (
            self.now - timedelta(hours=23, minutes=58) if room == "large" else None)
        self.assertEqual(self.check(), [])
        del cottage.above_since
        cottage.connection = "stale"                     # a short blip keeps the start
        self.assertEqual(self.check(1), [])
        cottage.connection = "ok"
        self.assertEqual(self.check(1), ["Mökki on kostea"])
        self.assertIn("(nyt 85 %)", self.notifier.sent[0][1])

    def test_humidity_limit_comes_from_the_settings(self):
        self.settings.set("site:cottage", "humidity_limit_pct", 90.0, "test")
        self.service.cottage.air["small"]["humidity"] = 86.0
        self.check()
        self.assertEqual(self.check(60 * 24), [])

    def test_air_reminder_only_while_someone_is_at_the_cottage(self):
        from types import SimpleNamespace
        self.service.cottage.air["large"]["co2"] = 1400
        self.assertEqual(self.check(), [])
        self.assertEqual(self.check(20), [])                     # no visit
        self.service.cottage.visit = SimpleNamespace(arrival=self.now + 30 * MINUTE)
        self.assertEqual(self.check(20), [])                     # not arrived yet
        self.check(10)
        self.assertEqual(self.check(9), [])
        self.assertEqual(self.check(1), ["Tuuleta: Mökki"])
        self.assertIn("1400 ppm", self.notifier.sent[0][1])
        self.service.cottage.air["large"]["co2"] = 1100
        self.assertEqual(len(self.check(30)), 1)                 # not yet fresh
        self.service.cottage.air["large"]["co2"] = 900
        self.assertEqual(len(self.check(1)), 1)                  # over: no notice
        self.service.cottage.air["large"]["co2"] = 1300
        self.check(1)
        self.assertEqual(self.check(10)[1:], ["Tuuleta: Mökki"])

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
                         ["Mökki on taas lämpimämpi"] * 2)


class EnergyGateway:
    """Two pumps on their Home Assistant devices; one has an energy sensor."""

    def __init__(self):
        self.asked = []
        self.rows = {"sensor.olohuone_energy_consumed_monthly": [
            (1793000000, {"change": 0.3, "state": 40.2}),
            (1793003600, {"change": -40.2, "state": 0.2}),     # the monthly reset
            (1793007200, {"change": 0.0, "state": 0.2})]}

    def registry(self):
        return {"entities": [
            {"entity_id": "climate.olohuone", "device_id": "dev-olo"},
            {"entity_id": "sensor.olohuone_energy_consumed_monthly", "device_id": "dev-olo"},
            {"entity_id": "sensor.olohuone_signal_strength", "device_id": "dev-olo"},
            {"entity_id": "climate.eteinen", "device_id": "dev-ete"}]}

    def raw_states(self):
        return {"sensor.olohuone_energy_consumed_monthly": {"attributes": {
                    "device_class": "energy", "unit_of_measurement": "kWh"}},
                "sensor.olohuone_signal_strength": {"attributes": {
                    "device_class": "signal_strength", "unit_of_measurement": "dBm"}}}

    def snapshot(self):
        return {"u-olo": {"kind": "climate", "entity_id": "climate.olohuone"},
                "u-ete": {"kind": "climate", "entity_id": "climate.eteinen"}}

    def statistics_rows(self, entity_id, start, types):
        self.asked.append((entity_id, start, types))
        return self.rows.get(entity_id, [])


class EnergyStore:
    def __init__(self):
        self.rows = {}

    def energy_upsert(self, unit_id, values):
        self.rows.setdefault(unit_id, []).extend(values)
        return len(values)


class EnergyRecorderTests(unittest.TestCase):
    def test_hourly_energy_from_the_pump_device_sensor(self):
        now = [datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)]
        gateway, store = EnergyGateway(), EnergyStore()
        recorder = EnergyRecorder(gateway, now=lambda: now[0])
        self.assertEqual(recorder.sensors(), {"u-olo": "sensor.olohuone_energy_consumed_monthly"})
        self.assertEqual(recorder.check(store), 2)
        self.assertEqual([(ts.timestamp(), kwh) for ts, kwh in store.rows["u-olo"]],
                         [(1793000000, 0.3), (1793003600, 0.2)])
        self.assertEqual(gateway.asked[0][1], now[0] - timedelta(days=35))
        # Once an hour, and after the first read only the last two days.
        now[0] += timedelta(minutes=30)
        self.assertEqual(recorder.check(store), 0)
        now[0] += timedelta(minutes=31)
        recorder.check(store)
        self.assertEqual(gateway.asked[-1][1], now[0] - timedelta(days=2))


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
            delivery = await asyncio.get_running_loop().run_in_executor(
                None, notifier.deliver, "Testi", "Viesti")
        self.assertEqual((delivery.sent, delivery.failed), (("tabletti",), ("ville",)))

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
        loop = asyncio.get_running_loop()
        notifier = HomeAssistantNotifier(self.make_gateway())
        with self.assertLogs("villa_kuppis.alerts", "WARNING"):
            self.assertFalse(await self.send(notifier))
        with self.assertLogs("villa_kuppis.alerts", "WARNING"):
            delivery = await loop.run_in_executor(None, notifier.deliver, "T", "V")
        self.assertEqual(delivery.reason, "unreachable")
        notifier = HomeAssistantNotifier(await self.start_gateway())
        with self.assertLogs("villa_kuppis.alerts", "WARNING"):
            self.assertFalse(await self.send(notifier))
        delivery = await loop.run_in_executor(None, notifier.deliver, "T", "V")
        self.assertEqual(delivery.reason, "no_phones")
        self.assertEqual([c for c in self.ha.calls if c[0] == "notify"], [])

    async def test_every_send_failing_is_its_own_reason(self):
        self.ha.services["notify"] = {"mobile_app_ville": {}}
        self.ha.fail["mobile_app_ville"] = ("home_assistant_error", "no token")
        notifier = HomeAssistantNotifier(await self.start_gateway())
        with self.assertLogs("villa_kuppis.alerts", "WARNING"):
            delivery = await asyncio.get_running_loop().run_in_executor(
                None, notifier.deliver, "T", "V")
        self.assertEqual((delivery.sent, delivery.failed, delivery.reason),
                         ((), ("ville",), "failed"))


if __name__ == "__main__":
    unittest.main()
