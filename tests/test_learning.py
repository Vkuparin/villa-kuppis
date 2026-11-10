"""Tests for learned room rates (learning.py and PumpService, card F5.1).

Synthetic rooms with known rates: readings every 5 minutes, quantized to
the pumps' 0.5 °C like the real sensors, against a daily outdoor curve.
No network: the forecast fetcher is a fake and the HTTP test is local.
"""

from __future__ import annotations

import json
import math
import random
import tempfile
import time
import unittest
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from villa_kuppis import learning
from villa_kuppis.learning import Outdoor, Reading, fit, simulate, step, validate
from villa_kuppis.pump_app import PumpAppServer
from villa_kuppis.pump_probe import FakeClient
from villa_kuppis.pump_service import PumpService

START = int(datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp())
DAYS = 14


def outdoor_curve(mean=24.0, swing=6.0, days=DAYS):
    return [(START + h * 3600,
             mean + swing * math.sin(((h % 24) - 9) / 24 * 2 * math.pi))
            for h in range(days * 24 + 1)]


def simulate_room(a=0.08, b=0.3, cool=2.0, heat=None, mode="Cool",
                  setpoint=22.0, on_hours=(8, 20), outdoor=None,
                  days=DAYS, start_room=24.0, seed=1):
    """Readings of a room driven by the first-order model."""
    rng = random.Random(seed)
    out = Outdoor(outdoor or outdoor_curve(days=days))
    rows, room = [], start_room
    for k in range(days * 288):
        ts = START + k * 300
        hour = (k // 12) % 24
        # Every third day the unit stays off, so the passive drift is seen
        # at all hours.
        power = on_hours[0] <= hour < on_hours[1] and (k // 288) % 3 != 2
        free = room + (a * (out.at(ts) - room) + b) * 300 / 3600
        if power and mode == "Cool" and free > setpoint:
            free = max(setpoint, free - cool * 300 / 3600)
        if power and mode == "Heat" and free < setpoint:
            free = min(setpoint, free + heat * 300 / 3600)
        room = free + rng.gauss(0, 0.02)
        rows.append(Reading(ts, round(room * 2) / 2, setpoint, power, mode))
    return rows, out


def heated_room(days, heat=3.0, holding_from=None, slow_from=None, a=0.05):
    """Readings of a heated room: 15 °C on most evenings, 5 °C otherwise.

    From day ``holding_from`` the room only holds 5 °C; from day
    ``slow_from`` the heaters heat at a fifth of ``heat``.
    """
    out = Outdoor(outdoor_curve(mean=0.0, swing=4.0, days=days))
    rows, room = [], 8.0
    for k in range(days * 288):
        ts = START + k * 300
        day, hour = k // 288, (k // 12) % 24
        evening = 16 <= hour < 22 and day % 3 != 2
        target = 15.0 if evening and (holding_from is None or day < holding_from) else 5.0
        rate = heat / 5 if slow_from is not None and day >= slow_from else heat
        free = room + a * (out.at(ts) - room) * 300 / 3600
        room = min(target, free + rate * 300 / 3600) if free < target else free
        rows.append(Reading(ts, round(room, 1), target, True, "Heat"))
    return rows, out


def after(days):
    return datetime.fromtimestamp(START + days * 86400 + 3600, timezone.utc)


class FitTests(unittest.TestCase):
    def test_recovers_cooling_room_rates(self):
        readings, out = simulate_room()
        model = fit(readings, out)
        self.assertTrue(model.ready)
        self.assertTrue(model.uses_outdoor)
        self.assertAlmostEqual(model.loss_per_h, 0.08, delta=0.01)
        self.assertAlmostEqual(model.drift_c_per_h, 0.3, delta=0.05)
        self.assertAlmostEqual(model.cool_c_per_h, 2.0, delta=0.3)
        self.assertIsNone(model.heat_c_per_h)

    def test_recovers_heating_room_rates(self):
        # A cold room heated in the evening, like the cottage heaters.
        readings, out = simulate_room(a=0.05, b=0.0, heat=1.5, mode="Heat",
                                      setpoint=21.0, on_hours=(16, 22),
                                      outdoor=outdoor_curve(mean=2.0, swing=4.0),
                                      start_room=12.0)
        model = fit(readings, out)
        self.assertTrue(model.ready)
        self.assertAlmostEqual(model.loss_per_h, 0.05, delta=0.01)
        self.assertAlmostEqual(model.heat_c_per_h, 1.5, delta=0.3)
        self.assertIsNone(model.cool_c_per_h)

    def test_one_day_is_too_little(self):
        readings, out = simulate_room(days=1)
        model = fit(readings, out)
        self.assertFalse(model.ready)
        self.assertEqual(model.reason, "too_little_data")
        self.assertEqual(fit([], out).reason, "no_data")

    def test_without_outdoor_data_learns_drift_only(self):
        readings, _out = simulate_room()
        model = fit(readings, Outdoor([]))
        self.assertTrue(model.ready)
        self.assertFalse(model.uses_outdoor)
        self.assertEqual(model.loss_per_h, 0.0)
        self.assertIsNotNone(model.cool_c_per_h)

    def test_holding_and_unknown_modes_are_skipped(self):
        # A unit holding its setpoint all the time says nothing.
        readings = [Reading(START + k * 300, 22.0, 22.0, True, "Cool")
                    for k in range(5 * 288)]
        readings += [Reading(START + 5 * 86400 + k * 300, 24.0, 22.0, True, "Dry")
                     for k in range(288)]
        self.assertFalse(fit(readings, Outdoor(outdoor_curve())).ready)


class SimulationTests(unittest.TestCase):
    MODEL = learning.RoomModel(True, loss_per_h=0.1, drift_c_per_h=0.0,
                               cool_c_per_h=2.0, uses_outdoor=True)

    def test_cooling_stops_at_the_setpoint(self):
        room = 25.0
        for _ in range(12):
            room = step(self.MODEL, room, 25.0, True, "Cool", 22.0, 900)
        self.assertEqual(room, 22.0)

    def test_off_room_moves_towards_outdoor(self):
        path = simulate(self.MODEL, 20.0, START, START + 6 * 3600,
                        lambda _ts: (False, None, None),
                        Outdoor([(START, 30.0), (START + 7 * 3600, 30.0)]))
        self.assertEqual(len(path), 25)
        self.assertGreater(path[-1][1], 24.0)
        self.assertLess(path[-1][1], 30.0)


class ValidationTests(unittest.TestCase):
    def test_held_out_day_is_within_the_agreed_error(self):
        readings, out = simulate_room()
        model, check = validate(readings, out, after(DAYS))
        self.assertTrue(model.ready)
        self.assertEqual(check.day, "2026-07-14")
        self.assertGreater(check.points, 90)
        self.assertLessEqual(check.mae_c, learning.MAX_MAE_C)
        self.assertTrue(check.passed)

    def test_held_out_day_is_not_used_for_training(self):
        readings, out = simulate_room()
        _model, check = validate(readings, out, after(DAYS))
        day_start = START + (DAYS - 1) * 86400 - 3 * 3600  # Helsinki midnight
        # A room that suddenly loses heat fast (an open window) on the
        # held-out day cannot be predicted from the earlier days.
        changed = [r if r.ts < day_start else
                   Reading(r.ts, r.room_c - min(6.0, (r.ts - day_start) / 3600),
                           r.setpoint_c, False, r.mode)
                   for r in readings]
        _model, worse = validate(changed, out, after(DAYS))
        self.assertEqual(worse.day, check.day)
        self.assertFalse(worse.passed)
        self.assertGreater(worse.mae_c, learning.MAX_MAE_C)

    def test_a_period_that_only_holds_the_setpoint_does_not_pass(self):
        # Stand-in days with evening heat-ups, then a week of just holding
        # 5 °C: the holding days say nothing about the heaters' power.
        readings, out = heated_room(days=18, holding_from=10)
        _model, check = validate(readings, out, after(18))
        self.assertFalse(check.passed)
        self.assertEqual(check.reason, "few_moves")
        self.assertEqual(check.days, learning.MAX_HELD_DAYS)

    def test_a_cottage_room_holds_out_weeks_back_to_its_last_visit(self):
        # Cottage rooms heat only on visits: the held-out period may reach
        # back to the last heat-up eight days ago.
        readings, out = heated_room(days=18, holding_from=10)
        _model, check = validate(readings, out, after(18), learning.HEAT_MAX_HELD_DAYS)
        self.assertTrue(check.passed, check)
        self.assertGreater(check.days, learning.MAX_HELD_DAYS)

    def test_a_wrong_heating_rate_fails_on_a_heat_up(self):
        readings, out = heated_room(days=14, slow_from=13)
        model, check = validate(readings, out, after(14))
        self.assertGreater(model.heat_c_per_h, 2.0)
        self.assertFalse(check.passed)
        self.assertEqual(check.reason, "")
        self.assertGreater(check.mae_c, learning.MAX_MAE_C)
        same, _out = heated_room(days=14)
        self.assertTrue(validate(same, out, after(14))[1].passed)

    def test_no_complete_day_means_no_check(self):
        readings, out = simulate_room(days=1)
        _model, check = validate(readings, out, after(1))
        self.assertIsNone(check)


def cottage_rows(days=DAYS, a=0.05, heat=1.5):
    """A cottage room heated to 15 °C on most evenings, 5 °C otherwise, as
    the stored temperature and status sensor rows of the large room."""
    out = Outdoor(outdoor_curve(mean=0.0, swing=4.0, days=days))
    rows, room = [], 8.0
    for k in range(days * 288):
        ts = START + k * 300
        hour = (k // 12) % 24
        target = 15.0 if 16 <= hour < 22 and (k // 288) % 3 != 2 else 5.0
        free = room + a * (out.at(ts) - room) * 300 / 3600
        room = min(target, free + heat * 300 / 3600) if free < target else free
        rows.append(("cottage", "large-temperature", ts, round(room, 1), None, None))
        rows.append(("cottage", "large-status", ts, target, "follow", None))
        # Driven high (target + 3) up to the target, low (5 °C) above it.
        setpoint = target + 3.0 if room <= target else 5.0
        rows.extend(("cottage", f"large-heater-{n}", ts, None, "heat", setpoint)
                    for n in (1, 2, 3))
    return rows, out


class CottageLearningTests(unittest.TestCase):
    """Card F5.2: the cottage rooms learn from their sensor and target rows."""

    def setUp(self):
        from villa_kuppis.cottage import CottageSite, FakeSiteGateway
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.now = after(DAYS)
        now = lambda: self.now

        def factory(data):
            gateway = FakeSiteGateway(now=now)
            return CottageSite(data, gateway, gateway.bindings, now=now, sample_interval=3600)
        self.service = PumpService(Path(self._tmp.name), client=FakeClient([]), now=now,
                                   cottage_factory=factory)
        self.addCleanup(self.service.close)
        self.store = self.service.cottage._store

    def insert(self, rows):
        rows = [r if len(r) == 6 else (*r, None) for r in rows]
        with self.store._lock, self.store._conn:
            self.store._conn.executemany(
                "INSERT INTO site_reading (site_id, device_id, ts_utc, value, mode, setpoint) "
                "VALUES (?, ?, ?, ?, ?, ?)", rows)

    def heater(self, ts, n, mode, setpoint=None):
        return ("cottage", f"large-heater-{n}", ts, None, mode, setpoint)

    def test_readings_follow_the_heaters_and_the_target_in_force(self):
        t = START
        self.insert([("cottage", "large-temperature", t, 6.0, None),
                     *(self.heater(t + 10 * n, n, "heat", 15.0) for n in (1, 2, 3)),
                     ("cottage", "large-status", t + 60, 12.0, "follow"),
                     self.heater(t + 120, 3, "off"),
                     self.heater(t + 180, 1, "off"),
                     self.heater(t + 190, 2, "off"),
                     ("cottage", "large-status", t + 600, None, "manual"),
                     ("cottage", "large-status", t + 2000, 5.0, "fallback")])
        start = datetime.fromtimestamp(START, timezone.utc)
        end = start + timedelta(seconds=4000)
        rows = self.service.cottage.learning_readings("large", start, end)
        self.assertEqual([(r.room_c, r.setpoint_c, r.power, r.mode) for r in rows], [
            (6.0, None, None, None),          # heaters not reported yet
            (6.0, None, None, None),          # no target yet
            (6.0, None, None, None),
            (6.0, None, None, None),
            (6.0, 12.0, True, "Heat"),        # every heater towards the target
            (6.0, None, None, None),          # one heater off: partial power
            (6.0, None, None, None),
            (6.0, None, False, None),         # every heater off: no heating
            (6.0, None, False, None),         # manual, but still all off
            (None, None, None, None)])        # readings too old
        self.assertEqual(self.service.cottage.learning_readings("small", start, end), [])

    def test_fresh_start_forgets_the_earlier_history(self):
        rows, _out = cottage_rows(days=2)
        self.insert(rows)
        start = datetime.fromtimestamp(START, timezone.utc)
        end = start + timedelta(days=2)
        site = self.service.cottage
        self.assertGreater(len(site.learning_readings("large", start, end)), 500)
        site.restart_learning("large", "test")    # at self.now, after the rows
        self.assertEqual(site.learning_readings("large", start, end), [])
        self.assertEqual(site.learning_since("large"), self.now)
        self.assertEqual(site._store.events(1)[0]["action"], "learning")

    def test_fresh_start_keeps_the_history_after_it(self):
        # A restart inside the window: the rows after it still come back
        # (an int start once raised TypeError in every target tick).
        rows, _out = cottage_rows(days=2)
        self.insert(rows)
        start = datetime.fromtimestamp(START, timezone.utc)
        end = start + timedelta(days=2)
        site = self.service.cottage
        every = site.learning_readings("large", start, end)
        saved, self.now = self.now, start + timedelta(days=1)
        site.restart_learning("large", "test")
        self.now = saved
        later = site.learning_readings("large", start, end)
        self.assertTrue(0 < len(later) < len(every))
        self.assertTrue(all(r.ts >= int((start + timedelta(days=1)).timestamp()) for r in later))
        self.service.room_rates()                 # learns from it without raising

    def test_cottage_rooms_learn_their_heating_rates(self):
        rows, out = cottage_rows()
        self.insert(rows)
        self.service.store.outdoor_upsert("cottage", [
            (datetime.fromtimestamp(ts, timezone.utc), v)
            for ts, v in outdoor_curve(mean=0.0, swing=4.0)])
        units = {u["id"]: u for u in self.service.room_rates()["units"]}
        large, small = units["cottage:large"], units["cottage:small"]
        self.assertEqual((large["site"], large["name"]), ("cottage", "Mökki"))
        self.assertTrue(large["model"]["ready"])
        self.assertTrue(large["model"]["uses_outdoor"])
        self.assertAlmostEqual(large["model"]["loss_per_h"], 0.05, delta=0.01)
        self.assertAlmostEqual(large["model"]["heat_c_per_h"], 1.5, delta=0.3)
        self.assertTrue(large["validation"]["passed"])
        self.assertFalse(small["model"]["ready"])
        models, _outdoor, _ahead = self.service.learned_models("cottage")
        self.assertEqual(set(models), {"large", "small"})
        self.assertTrue(models["large"].ready)
        # The home units are learned apart from the cottage rooms.
        self.assertEqual(self.service.learned_models("home")[0], {})
        # Only a room whose held-out day passed plans a visit heat-up.
        trusted, _future = self.service.cottage_heating_models()
        self.assertEqual(set(trusted), {"large"})

    def test_every_cottage_room_has_a_heat_model_from_the_start(self):
        # No statistics in the stand-in cottage: each room starts from its
        # floor area, with the power of the heaters that can heat now.
        from villa_kuppis import thermal
        models, _future = self.service.cottage_thermal_models()
        self.assertEqual(set(models), {"large", "small"})
        model, kw = models["large"]
        self.assertTrue(model.ready and model.uses_outdoor)
        self.assertAlmostEqual(kw, self.service.cottage.heat_kw("large"))
        self.assertGreater(kw, 0)
        prior = thermal.Prior.for_area(35.0)
        self.assertAlmostEqual(model.loss_per_h, prior.ua_kw_k / prior.c_kwh_k)
        self.assertAlmostEqual(model.heat_c_per_h, kw / prior.c_kwh_k)
        units = {u["id"]: u for u in self.service.room_rates()["units"]}
        heat = units["cottage:small"]["heat"]
        self.assertEqual((heat["hours"], heat["ua_w_k"], heat["optimized"], heat["base_c"]),
                         (0, 22.4, True, 5.0))
        self.assertIsNone(heat["plan"])            # no prices here
        # A cottage that does not answer keeps the model it had.
        def away(*_args):
            raise OSError("cottage away")
        self.service._thermal_cache = None
        self.service.cottage.thermal_inputs = away
        with self.assertLogs(level="WARNING"):
            again, _future = self.service.cottage_thermal_models()
        self.assertEqual(again["large"][0].loss_per_h, model.loss_per_h)


def _state(unit_id, name, room, updated):
    return {"id": unit_id, "name": name, "power": True, "mode": "Cool",
            "setpoint": 22.0, "fan": "Auto", "room_temp": room,
            "in_error": False, "caps": None, "updated_at": updated}


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.now = after(DAYS)
        self.service = PumpService(Path(self._tmp.name), client=FakeClient([]),
                                   now=lambda: self.now)
        self.addCleanup(self.service.close)

    def _seed(self):
        readings, _out = simulate_room()
        store = self.service.store
        store.upsert_units([_state("u1", "Olohuone", 24.0, self.now.isoformat()),
                            _state("u2", "Uusi", 23.0, self.now.isoformat())])
        with store._lock:
            store._conn.executemany(
                "INSERT INTO pump_history (ts_utc, unit_id, power, mode, "
                "setpoint, fan, room_temp) VALUES (?, 'u1', ?, ?, ?, 'Auto', ?)",
                [(r.ts, r.power, r.mode, r.setpoint_c, r.room_c) for r in readings])
            store._conn.commit()
        store.outdoor_upsert("home", [
            (datetime.fromtimestamp(ts, timezone.utc), v) for ts, v in outdoor_curve()])
        store.forecast_upsert("home", [
            (self.now + timedelta(hours=h), 15.0 + h) for h in range(48)])

    def test_room_rates_payload(self):
        self._seed()
        payload = self.service.room_rates()
        self.assertEqual(payload["limit_c"], learning.MAX_MAE_C)
        self.assertEqual(len(payload["forecast"]), 24)
        learned, new = payload["units"]
        self.assertTrue(learned["model"]["ready"])
        self.assertAlmostEqual(learned["model"]["cool_c_per_h"], 2.0, delta=0.3)
        self.assertTrue(learned["validation"]["passed"])
        self.assertEqual(learned["now"]["outdoor_c"], 15.0)
        # With 15 °C outdoors a 24 °C room cools down when the unit is off.
        self.assertLess(learned["now"]["off_c_per_h"], 0)
        self.assertLess(learned["now"]["cooling_c_per_h"],
                        learned["now"]["off_c_per_h"])
        self.assertTrue(learned["predicted"]["forecast"])
        self.assertLess(learned["predicted"]["off_c"], 24.0)
        self.assertFalse(new["model"]["ready"])
        self.assertIsNone(new["validation"])
        self.assertIsNone(new["now"])
        json.dumps(payload)

    def test_room_rates_are_cached(self):
        self._seed()
        first = self.service.room_rates()
        self.service.store.upsert_units([_state("u3", "Kolmas", 22.0,
                                                self.now.isoformat())])
        self.assertIs(self.service.room_rates(), first)
        self.now += timedelta(minutes=11)
        self.assertEqual(len(self.service.room_rates()["units"]), 3)

    def test_weather_loop_stores_the_forecast(self):
        series = type("Series", (), {"values": [(self.now + timedelta(hours=1), 9.5)]})
        calls = []

        def forecast(lat, lon, start, end):
            calls.append((start, end))
            return series

        service = PumpService(
            Path(self._tmp.name) / "loop", client=FakeClient([]),
            now=lambda: self.now, weather_sites={"home": (60.1, 24.9)},
            weather_fetcher=lambda *_a: type("S", (), {"values": []}),
            forecast_fetcher=forecast, weather_interval=0.05)
        self.addCleanup(service.close)
        service.start()
        deadline = time.monotonic() + 3
        while not calls and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(calls)
        self.assertEqual(calls[0][1] - calls[0][0], timedelta(hours=48))
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not service.store.forecast_rows(
                "home", self.now, self.now + timedelta(hours=2)):
            time.sleep(0.02)
        self.assertEqual(service.store.forecast_rows(
            "home", self.now, self.now + timedelta(hours=2))[0][1], 9.5)

    def test_api_rooms_over_http(self):
        self._seed()
        server = PumpAppServer(self.service, port=0)
        self.addCleanup(server.server_close)
        import threading
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        with urllib.request.urlopen(server.url + "api/rooms", timeout=5) as resp:
            body = json.loads(resp.read())
        self.assertEqual([u["id"] for u in body["units"]], ["u1", "u2"])
        with urllib.request.urlopen(server.url, timeout=5) as resp:
            page = resp.read().decode("utf-8")
        self.assertIn("Huoneiden opitut nopeudet", page)
        self.assertIn('fetch("api/rooms")', page)


if __name__ == "__main__":
    unittest.main()
