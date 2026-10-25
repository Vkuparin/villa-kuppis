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

    def test_no_complete_day_means_no_check(self):
        readings, out = simulate_room(days=1)
        _model, check = validate(readings, out, after(1))
        self.assertIsNone(check)


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
