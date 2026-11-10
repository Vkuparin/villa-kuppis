"""Cottage price targets through Home Assistant (card H2.4).

A synthetic 24-hour FI price day goes into a temporary prices.db; a fake
cottage Home Assistant on localhost implements the villa_kuppis_cottage
set_target service the way the integration does (revision check, status
sensor attributes). The clock steps through every 15-minute period.
"""

from __future__ import annotations

import asyncio
import tempfile
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import unittest
from unittest import IsolatedAsyncioTestCase

from villa_kuppis.cottage_targets import CottageTargetSender, current_targets
from villa_kuppis.ha_gateway import BindingStore, HomeAssistantGateway
from villa_kuppis.planning import Period
from villa_kuppis.prices import INTERVAL_SECONDS, helsinki_day_bounds, validate_day
from villa_kuppis.storage import PriceStore

from tests.common import elering_payload
from tests.test_ha_gateway import TOKEN, Clock, FakeHA, eventually

DAY = date(2026, 11, 20)
# EUR/MWh per quarter of each hour: 2, 6, 9 and 15 c/kWh.
PATTERN = [20, 60, 90, 150]
EXPECTED = {20: 12.0, 60: 8.0, 90: 5.0, 150: 5.0}   # default cottage thresholds

# The thresholds these price fixtures were written for (the defaults until
# 0.4.35, when the defaults became heat only at negative prices), in the
# mode that uses thresholds (the default is Optimoitu, which has none).
RULES = {"heat_price_control": "fixed",
         "heat_rule1_price_c_kwh": 8.0, "heat_rule2_price_c_kwh": 5.0}


def with_rules(overrides):
    """``overrides`` with RULES under the cottage site unless set there."""
    out = dict(overrides)
    out["site:cottage"] = {**RULES, **overrides.get("site:cottage", {})}
    return out



class FakeCottageHA(FakeHA):
    """Adds the cottage integration's set_target service."""

    def __init__(self, clock):
        super().__init__(clock)
        self.targets: list[dict] = []
        self.revisions: dict[str, int] = {}
        self.schedules = False   # True: integration 0.3.0, which keeps a schedule

    async def _call(self, ws, data) -> None:
        if (data["domain"], data["service"]) != ("villa_kuppis_cottage", "set_target"):
            return await super()._call(ws, data)
        values = data["service_data"]
        assert "target" not in data   # a non-entity service: no target
        self.calls.append((data["domain"], data["service"], None, values))
        if self.hang:
            return
        room = values["room"]
        if values["revision"] < self.revisions.get(room, -1):
            await ws.send_json({"id": data["id"], "type": "result", "success": False,
                                "error": {"code": "service_validation_error",
                                          "message": "older revision"}})
            return
        self.revisions[room] = values["revision"]
        self.targets.append(values)
        await ws.send_json({"id": data["id"], "type": "result", "success": True,
                            "result": None})
        until = self.clock() + timedelta(minutes=values["valid_minutes"])
        attrs = {"mode": "follow", "requested_target": values["target"],
                 "revision": values["revision"], "valid_until": until.isoformat()}
        if self.schedules:
            attrs["schedule_steps"] = len(values.get("schedule") or ())
        self.set_state(f"sensor.villa_kuppis_{room}_target", str(max(values["target"], 7.0)),
                       attrs)


class TargetTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)   # after later VisitStore cleanups on Windows
        data = Path(self.tmp.name)
        start, end = helsinki_day_bounds(DAY)
        count = (end - start) // INTERVAL_SECONDS
        self.prices = [PATTERN[i % 4] for i in range(count)]
        intervals = validate_day(elering_payload(DAY, self.prices), DAY)
        with PriceStore(data / "prices.db") as store:
            store.replace_day(DAY.isoformat(), intervals, datetime.now(timezone.utc))
        self.day_start = start
        self.clock = Clock()
        self.clock.now = datetime.fromtimestamp(start, timezone.utc) + timedelta(minutes=1)
        self.ha = FakeCottageHA(self.clock)
        await self.ha.start()
        for room in ("large", "small"):
            self.ha.set_state(f"sensor.villa_kuppis_{room}_target", "7.0",
                              {"mode": "fallback", "revision": None}, push=False)
        self.bindings = BindingStore(data / "sites.db")
        self.bindings.bind("cottage", "large-status", "sensor.villa_kuppis_large_target", "sensor")
        self.bindings.bind("cottage", "small-status", "sensor.villa_kuppis_small_target", "sensor")
        self.gateway = HomeAssistantGateway(
            "cottage", self.ha.url, lambda: TOKEN, self.bindings, now=self.clock,
            command_timeout=0.5, confirm_timeout=0.5, retry_base=0.05, retry_max=0.2,
            site_stale_after=1e9)
        self.task = asyncio.create_task(self.gateway.run())
        await eventually(lambda: self.gateway.health()["state"] == "ok")
        self.automation = True
        self.overrides = {}
        self.sender = CottageTargetSender(
            self.gateway, {"large": "large-status", "small": "small-status"},
            prices_db=data / "prices.db", overrides=lambda: with_rules(self.overrides),
            automation_on=lambda: self.automation, now=self.clock)

    async def asyncTearDown(self):
        self.gateway.request_stop()
        await asyncio.wait_for(self.task, 3.0)
        await self.ha.stop()
        self.bindings.close()

    async def settle(self, sent):
        for cmd in sent:
            if cmd["state"] == "pending":
                await self.gateway.wait(cmd["request_id"], 3.0)

    async def enable_schedules(self):
        self.ha.schedules = True
        for room in ("large", "small"):
            self.ha.set_state(f"sensor.villa_kuppis_{room}_target", "7.0",
                              {"mode": "fallback", "revision": None, "schedule_steps": 0})
        await eventually(lambda: all("schedule_steps" in (
            self.gateway.snapshot()[f"{room}-status"].get("attributes") or {})
            for room in ("large", "small")))

    async def test_whole_day_applies_the_right_targets_with_acknowledgements(self):
        for i in range(len(self.prices)):
            sent = self.sender.tick()
            self.assertEqual(len(sent), 2, f"period {i}")
            await self.settle(sent)
            for cmd in sent:
                self.assertEqual(self.gateway.command(cmd["request_id"])["state"], "confirmed")
            # A second tick in the same period sends nothing new.
            self.clock.advance(60)
            self.assertEqual(self.sender.tick(), [])
            self.clock.advance(INTERVAL_SECONDS - 60)
        large = [t for t in self.ha.targets if t["room"] == "large"]
        self.assertEqual(len(large), len(self.prices))
        for i, (t, price) in enumerate(zip(large, self.prices)):
            self.assertEqual(t["target"], EXPECTED[price], f"period {i}")
            self.assertEqual(t["revision"], self.day_start + i * INTERVAL_SECONDS)
            self.assertEqual(t["valid_minutes"], 45)

    async def test_room_override_reaches_its_target(self):
        self.overrides = {"room:cottage/small": {"heat_base_c": 10.0}}
        await self.settle(self.sender.tick())
        self.clock.advance(INTERVAL_SECONDS * 2)   # a 9 c/kWh period: base only
        await self.settle(self.sender.tick())
        latest = {t["room"]: t["target"] for t in self.ha.targets}
        self.assertEqual(latest, {"large": 5.0, "small": 10.0})

    async def test_automation_off_after_a_restart_releases_each_room_once(self):
        # A new sender (Villa Kuppis restarted) does not know what the
        # cottage follows: it releases every room once, then stays quiet.
        self.automation = False
        sent = self.sender.tick()
        self.assertEqual(len(sent), 2)
        await self.settle(sent)
        self.assertEqual({(t["room"], t["target"], t["revision"]) for t in self.ha.targets},
                         {("large", 0.0, self.day_start), ("small", 0.0, self.day_start)})
        self.clock.advance(INTERVAL_SECONDS)
        self.assertEqual(self.sender.tick(), [])
        self.assertEqual(len(self.ha.targets), 2)

    async def test_hand_target_wins_and_is_sent_with_automation_off(self):
        hand = {}
        self.sender._hand_targets = lambda: hand
        await self.enable_schedules()
        await self.settle(self.sender.tick())
        hand["large"] = 19.5
        sent = self.sender.tick()
        self.assertEqual([c["value"]["room"] for c in sent], ["large"])
        await self.settle(sent)
        t = self.ha.targets[-1]
        self.assertEqual((t["room"], t["target"]), ("large", 19.5))
        # The whole schedule holds it: an outage does not drop the room to the plan.
        self.assertTrue(all(step["target"] == 19.5 for step in t["schedule"]))
        self.assertEqual(self.sender.last["large"].reason, "hand")
        # Automaatio off: the hand room keeps its target, the other is released.
        self.automation = False
        self.clock.advance(INTERVAL_SECONDS)
        sent = self.sender.tick()
        await self.settle(sent)
        latest = {t["room"]: t["target"] for t in self.ha.targets[-2:]}
        self.assertEqual(latest, {"large": 19.5, "small": 0.0})
        # Removing it with Automaatio off releases the room.
        del hand["large"]
        sent = self.sender.tick()
        self.assertEqual([(c["value"]["room"], c["value"]["target"]) for c in sent],
                         [("large", 0.0)])

    async def test_refused_release_is_not_repeated(self):
        self.ha.revisions = {"large": self.day_start + 10 * INTERVAL_SECONDS}
        self.automation = False
        await self.settle(self.sender.tick())
        calls = len(self.ha.calls)
        self.clock.advance(5 * 60)
        self.assertEqual(self.sender.tick(), [])
        self.assertEqual(len(self.ha.calls), calls)

    async def test_automation_off_releases_each_room_once_and_on_resumes(self):
        self.ha.schedules = True
        await self.settle(self.sender.tick())
        self.automation = False
        sent = self.sender.tick()
        self.assertEqual(len(sent), 2)
        await self.settle(sent)
        for cmd in sent:
            self.assertEqual(self.gateway.command(cmd["request_id"])["state"], "confirmed")
        # Below every safe setpoint, a minute long and without the plan: the
        # cottage drops to its fallback instead of following the old schedule.
        released = {t["room"]: t for t in self.ha.targets[-2:]}
        for t in released.values():
            self.assertEqual((t["target"], t["valid_minutes"], t["revision"]),
                             (0.0, 1, self.day_start))
            self.assertNotIn("schedule", t)
        self.clock.advance(INTERVAL_SECONDS)
        self.assertEqual(self.sender.tick(), [])          # once is enough
        self.automation = True
        await self.settle(self.sender.tick())
        latest = {t["room"]: t for t in self.ha.targets[-2:]}
        self.assertEqual(latest["large"]["revision"], self.day_start + INTERVAL_SECONDS)
        self.assertGreater(latest["large"]["target"], 0.0)
        self.assertIn("schedule", latest["large"])

    async def test_disconnected_sends_nothing_then_only_the_current_target(self):
        await self.settle(self.sender.tick())
        await self.ha.drop()
        await eventually(lambda: self.gateway.health()["state"] != "ok")
        self.clock.advance(INTERVAL_SECONDS)
        self.assertEqual(self.sender.tick(), [])
        self.clock.advance(INTERVAL_SECONDS * 2)   # two periods missed
        await eventually(lambda: self.gateway.health()["state"] == "ok")
        await self.settle(self.sender.tick())
        revisions = [t["revision"] for t in self.ha.targets if t["room"] == "large"]
        self.assertEqual(revisions, [self.day_start, self.day_start + 3 * INTERVAL_SECONDS])

    async def test_unacknowledged_write_is_retried_after_a_minute(self):
        self.ha.hang = True
        sent = self.sender.tick()
        await self.settle(sent)
        self.assertEqual(self.gateway.command(sent[0]["request_id"])["state"], "unknown")
        self.ha.hang = False
        self.assertEqual(self.sender.tick(), [])          # too soon
        self.clock.advance(61)
        retried = self.sender.tick()
        self.assertEqual(len(retried), 2)
        await self.settle(retried)
        self.assertEqual(self.gateway.command(retried[0]["request_id"])["state"], "confirmed")

    async def test_visit_replaces_the_plan_from_its_start(self):
        from villa_kuppis.visit import VisitStore
        visits = VisitStore(Path(self.tmp.name) / "visits.db")
        self.addCleanup(visits.close)
        self.sender._visits = visits
        arrival = self.clock() + timedelta(hours=14)
        visits.start(arrival, 21.0, ["large"], "Ville", self.clock())
        await self.settle(self.sender.tick())
        self.assertEqual(self.sender.last["large"].reason, "threshold")   # not started yet
        self.assertIsNone(visits.active().heat_start)
        self.clock.now = arrival - timedelta(hours=12)
        await self.settle(self.sender.tick())
        self.assertEqual(self.sender.last["large"].target_c, 21.0)
        self.assertEqual(self.sender.last["large"].reason, "visit")
        self.assertNotEqual(self.sender.last["small"].reason, "visit")
        self.assertIsNotNone(visits.active().heat_start)
        self.assertEqual(self.ha.targets[-2]["target"], 21.0)
        visits.end(self.clock())
        self.clock.advance(INTERVAL_SECONDS)
        await self.settle(self.sender.tick())
        self.assertNotEqual(self.sender.last["large"].reason, "visit")

    async def test_visit_is_sent_with_automation_off(self):
        from villa_kuppis.visit import VisitStore
        visits = VisitStore(Path(self.tmp.name) / "visits.db")
        self.addCleanup(visits.close)
        self.sender._visits = visits
        self.ha.schedules = True
        await self.settle(self.sender.tick())       # the cottage now keeps schedules
        self.automation = False
        arrival = self.clock() + timedelta(hours=14)
        departure = arrival + timedelta(hours=3)
        visits.start(arrival, 21.0, ["large"], "Ville", self.clock(), departure)
        sent = self.sender.tick()
        await self.settle(sent)
        latest = {t["room"]: t for t in self.ha.targets[-2:]}
        # Before the heat-up the visit room is at the fallback, with the
        # visit in its schedule and the fallback again after the departure.
        self.assertEqual(latest["large"]["target"], 0.0)
        targets = [step["target"] for step in latest["large"]["schedule"]]
        self.assertIn(21.0, targets)
        self.assertEqual(targets[-1], 0.0)
        self.assertEqual(set(targets) - {21.0}, {0.0})       # no economic heating
        self.assertEqual((latest["small"]["target"], latest["small"]["valid_minutes"]), (0.0, 1))
        self.clock.now = arrival - timedelta(hours=12)
        await self.settle(self.sender.tick())
        self.assertEqual((self.ha.targets[-1]["room"], self.ha.targets[-1]["target"]), ("large", 21.0))
        self.assertEqual(self.sender.last["large"].reason, "visit")
        # Ending the visit releases the room.
        visits.end(self.clock())
        sent = self.sender.tick()
        self.assertEqual([(c["value"]["room"], c["value"]["target"]) for c in sent],
                         [("large", 0.0)])

    async def test_visit_ends_by_itself_at_the_departure(self):
        from villa_kuppis.visit import VisitStore
        visits = VisitStore(Path(self.tmp.name) / "visits.db")
        self.addCleanup(visits.close)
        self.sender._visits = visits
        arrival = self.clock() + timedelta(hours=4)
        departure = arrival + timedelta(hours=3)
        visits.start(arrival, 21.0, ["large"], "Ville", self.clock(), departure)
        await self.settle(self.sender.tick())
        steps = self.sender.schedules["large"]
        visit_steps = [s.period_ts for s in steps if s.reason == "visit"]
        self.assertTrue(visit_steps)
        self.assertLess(max(visit_steps), departure.timestamp())
        self.assertGreater(steps[-1].period_ts, departure.timestamp())   # plan goes past it
        self.clock.now = departure
        await self.settle(self.sender.tick())
        self.assertIsNone(visits.active())
        self.assertNotEqual(self.sender.last["large"].reason, "visit")
        self.assertIsNone(self.sender.visit_plan)

    async def test_humidity_guard_heats_a_damp_room_until_it_dries(self):
        humidity = {"large": 85.0, "small": 60.0}
        self.sender._room_humidity = lambda: dict(humidity)
        lowest = lambda room="large": min(t.target_c for t in self.sender.plans()[room])
        self.overrides = {"site:cottage": {"humidity_heat": False}}
        plain = [t.target_c for t in self.sender.plans()["large"]]
        self.assertEqual(min(plain), 5.0)                     # switched off
        self.overrides = {}                                   # on by default
        plans = self.sender.plans()
        self.assertEqual([t.target_c for t in plans["large"]], [max(v, 7.0) for v in plain])
        self.assertEqual({t.reason for t in plans["large"] if t.target_c == 7.0}, {"humidity"})
        self.assertEqual(lowest("small"), 5.0)
        humidity["large"] = 78.0          # under the limit, but not 5 points under
        self.assertEqual(lowest(), 7.0)
        del humidity["large"]             # no reading: as it was
        self.assertEqual(lowest(), 7.0)
        humidity["large"] = 74.0
        self.assertEqual(lowest(), 5.0)
        self.assertEqual(self.sender.damp, set())

    async def test_plan_goes_with_the_target_to_an_integration_that_keeps_it(self):
        self.ha.schedules = True
        for room in ("large", "small"):
            self.ha.set_state(f"sensor.villa_kuppis_{room}_target", "7.0",
                              {"mode": "fallback", "revision": None, "schedule_steps": 0})
        await eventually(lambda: "schedule_steps" in (
            self.gateway.snapshot()["large-status"].get("attributes") or {}))
        self.clock.advance(INTERVAL_SECONDS * 4 * 20)   # 20:01 local: 15 periods left
        await self.settle(self.sender.tick())
        sent = self.ha.targets[-2]
        self.assertEqual(sent["room"], "large")
        steps = sent["schedule"]
        # Only the changes, and the last period, which ends the plan.
        first = self.day_start + 81 * INTERVAL_SECONDS
        expected = [(first + k * INTERVAL_SECONDS, EXPECTED[PATTERN[(81 + k) % 4]])
                    for k in range(len(self.prices) - 81)]
        expected = [e for i, e in enumerate(expected)
                    if i == len(expected) - 1 or e[1] != (expected[i - 1][1] if i else sent["target"])]
        self.assertEqual([(int(datetime.fromisoformat(s["start"]).timestamp()), s["target"])
                          for s in steps], expected)

    async def test_older_integration_gets_the_target_alone(self):
        await self.settle(self.sender.tick())
        self.assertNotIn("schedule", self.ha.targets[-1])

    async def test_changed_future_plan_is_sent_in_the_same_period(self):
        await self.enable_schedules()
        await self.settle(self.sender.tick())
        first = next(t for t in self.ha.targets if t["room"] == "small")
        self.assertEqual(first["target"], 12.0)
        self.assertIn(8.0, [s["target"] for s in first["schedule"]])
        # Only later periods change: the current cheap period stays at 12 °C.
        self.overrides = {"room:cottage/small": {"heat_rule1_c": 10.0}}
        updated = self.sender.tick()
        self.assertEqual([c["value"]["room"] for c in updated], ["small"])
        await self.settle(updated)
        sent = self.ha.targets[-1]
        self.assertEqual((sent["target"], sent["revision"]), (first["target"], first["revision"]))
        self.assertIn(10.0, [s["target"] for s in sent["schedule"]])
        self.assertNotIn(8.0, [s["target"] for s in sent["schedule"]])
        self.assertEqual(self.sender.tick(), [])

    async def test_plan_is_sent_when_integration_gains_schedule_support(self):
        await self.settle(self.sender.tick())
        self.assertNotIn("schedule", self.ha.targets[-1])
        self.ha.schedules = True
        self.ha.set_state("sensor.villa_kuppis_large_target", "12.0", {
            "requested_target": 12.0, "revision": self.day_start, "schedule_steps": 0})
        await eventually(lambda: "schedule_steps" in (
            self.gateway.snapshot()["large-status"].get("attributes") or {}))
        updated = self.sender.tick()
        self.assertEqual([c["value"]["room"] for c in updated], ["large"])
        await self.settle(updated)
        self.assertTrue(self.ha.targets[-1]["schedule"])
        self.assertEqual(self.sender.tick(), [])

    async def test_changed_schedule_waits_for_a_pending_write(self):
        await self.enable_schedules()
        self.ha.hang = True
        pending = self.sender.tick()
        self.overrides = {"room:cottage/small": {"heat_rule1_c": 10.0}}
        self.assertEqual(self.sender.tick(), [])
        await self.settle(pending)
        self.ha.hang = False
        updated = self.sender.tick()
        self.assertEqual([c["value"]["room"] for c in updated], ["small"])
        await self.settle(updated)
        self.assertIn(10.0, [s["target"] for s in self.ha.targets[-1]["schedule"]])

    async def test_automation_off_and_on_restores_plan_in_the_same_period(self):
        await self.enable_schedules()
        await self.settle(self.sender.tick())
        self.automation = False
        await self.settle(self.sender.tick())
        self.automation = True
        restored = self.sender.tick()
        self.assertEqual(len(restored), 2)
        await self.settle(restored)
        self.assertTrue(all(t["target"] == 12.0 and t["schedule"] for t in self.ha.targets[-2:]))
        self.assertEqual(self.sender.tick(), [])

    async def test_schedule_holds_a_visit_heat_up(self):
        from villa_kuppis.visit import VisitStore
        visits = VisitStore(Path(self.tmp.name) / "visits.db")
        self.addCleanup(visits.close)
        self.sender._visits = visits
        self.ha.schedules = True
        self.ha.set_state("sensor.villa_kuppis_large_target", "7.0",
                          {"mode": "fallback", "revision": None, "schedule_steps": 0})
        await eventually(lambda: "schedule_steps" in (
            self.gateway.snapshot()["large-status"].get("attributes") or {}))
        visits.start(self.clock() + timedelta(hours=16), 21.0, ["large"], "Ville", self.clock())
        await self.settle(self.sender.tick())
        sent = [t for t in self.ha.targets if t["room"] == "large"][-1]
        start = self.sender.visit_plan.start
        self.assertGreater(start, self.clock())
        steps = [(datetime.fromisoformat(s["start"]), s["target"]) for s in sent["schedule"]]
        heat = [at for at, value in steps if value == 21.0]
        self.assertTrue(heat)
        self.assertLessEqual(heat[0], start)
        self.assertGreater(heat[0], start - timedelta(seconds=INTERVAL_SECONDS))
        self.assertTrue(all(value == 21.0 for at, value in steps if at >= heat[0]))

    async def test_missing_prices_fall_back_to_the_base_level(self):
        self.clock.now = datetime(2026, 11, 22, 10, 0, tzinfo=timezone.utc)
        await self.settle(self.sender.tick())
        self.assertEqual({t["target"] for t in self.ha.targets}, {5.0})
        self.assertEqual(self.sender.last["large"].reason, "noprice")


class PureTests(unittest.TestCase):
    def test_current_targets_pick_the_period_containing_now(self):
        periods = [Period(900 * k, Decimal(p)) for k, p in enumerate(("2", "6", "9"))]
        targets = current_targets(with_rules({}), periods, ["large"], 900 + 899)
        self.assertEqual(targets["large"].target_c, 8.0)
        self.assertEqual(targets["large"].period_ts, 900)
        self.assertEqual(targets["large"].reason, "threshold")


class OptimizedTargetTests(unittest.TestCase):
    """Card F5.2: an Optimoitu room takes the heating optimizer's target."""

    def setUp(self):
        from villa_kuppis.learning import Outdoor, RoomModel
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        data = Path(self.tmp.name)
        start, end = helsinki_day_bounds(DAY)
        count = (end - start) // INTERVAL_SECONDS
        intervals = validate_day(elering_payload(DAY, [PATTERN[i % 4] for i in range(count)]), DAY)
        with PriceStore(data / "prices.db") as store:
            store.replace_day(DAY.isoformat(), intervals, datetime.now(timezone.utc))
        self.now = datetime.fromtimestamp(start, timezone.utc) + timedelta(minutes=1)
        self.overrides = {}
        self.temps = {"large": 6.0, "small": 6.0}
        self.humidity = {}
        self.calls = 0
        # The visit heat-up's learned room, and the maintenance plan's heat
        # balance: a 35 m² room with 2.5 kW of heaters in frost.
        learned = RoomModel(True, drift_c_per_h=-0.5, heat_c_per_h=2.0)
        balance = RoomModel(True, loss_per_h=0.07, heat_c_per_h=1.9, uses_outdoor=True)
        start_ts = int(self.now.timestamp())
        outdoor = Outdoor([(start_ts + h * 3600, -5.0) for h in range(-2, 50)])

        def thermal():
            self.calls += 1
            return {"large": (balance, 2.5)}, outdoor
        self.sender = CottageTargetSender(
            None, {"large": "large-status", "small": "small-status"},
            prices_db=data / "prices.db", overrides=lambda: self.overrides,
            automation_on=lambda: True, now=lambda: self.now,
            models=lambda: ({"large": learned}, Outdoor([])), thermal=thermal,
            room_temps=lambda: self.temps, room_humidity=lambda: self.humidity)

    def test_room_stores_heat_in_cheap_periods_within_its_band(self):
        by_price = {}
        for i in range(8):
            targets = self.sender.targets()
            self.assertEqual(targets["small"].reason, "base")   # no heater power known
            self.assertEqual(targets["small"].target_c, 5.0)
            large = targets["large"]
            self.assertGreaterEqual(large.target_c, 5.0)
            self.assertLessEqual(large.target_c, 8.0)
            self.assertEqual(large.reason, "optimized" if large.target_c > 5.0 else "base")
            by_price.setdefault(PATTERN[i % 4], set()).add(large.target_c)
            self.now += timedelta(seconds=INTERVAL_SECONDS)
        # 2, 6, 9 and 15 c quarters: heat goes in at 2 c, never at 15 c.
        self.assertGreater(min(by_price[20]), 5.0)
        self.assertEqual(by_price[150], {5.0})
        # Planned once per period, not on every call.
        calls = self.calls
        self.sender.targets()
        self.sender.targets()
        self.assertEqual(self.calls, calls + 1)

    def test_optimized_room_has_no_thresholds(self):
        # The fixed mode's thresholds would give 12 °C at 2 c/kWh.
        self.overrides = {"site:cottage": {"heat_rule1_price_c_kwh": 8.0,
                                           "heat_rule2_price_c_kwh": 5.0}}
        self.assertEqual(self.sender.targets()["small"].target_c, 5.0)
        self.assertLessEqual(self.sender.targets()["large"].target_c, 8.0)

    def test_fixed_setting_keeps_the_thresholds_and_unknown_temperature_the_base(self):
        self.overrides = with_rules({})
        large = self.sender.targets()["large"]
        self.assertEqual((large.reason, large.target_c), ("threshold", 12.0))
        self.overrides = {}
        self.temps = {}
        large = self.sender.targets()["large"]
        self.assertEqual((large.reason, large.target_c), ("base", 5.0))

    def test_failing_heat_model_holds_the_base_level(self):
        def broken():
            raise OSError("cottage away")
        self.sender._thermal = broken
        with self.assertLogs(level="WARNING"):
            large = self.sender.targets()["large"]
        self.assertEqual((large.reason, large.target_c), ("base", 5.0))

    def test_damp_room_is_planned_above_the_humidity_floor(self):
        self.humidity = {"large": 85.0}
        seen = set()
        for _ in range(8):
            large = self.sender.targets()["large"]
            self.assertGreaterEqual(large.target_c, 7.0)
            self.assertLessEqual(large.target_c, 8.0)
            self.assertEqual(large.reason, "optimized" if large.target_c > 7.0 else "humidity")
            seen.add(large.target_c)
            self.now += timedelta(seconds=INTERVAL_SECONDS)
        self.assertIn(7.0, seen)

    def test_band_comes_from_the_base_level_and_the_upper_limit(self):
        from villa_kuppis import settings
        from villa_kuppis.cottage_targets import heat_limits
        from villa_kuppis.heat_optimizer import HeatLimits
        values = settings.resolve({}, "cottage", "large")
        self.assertEqual(heat_limits(values), HeatLimits(5.0, 8.0))
        self.assertEqual(heat_limits(values, damp=True), HeatLimits(7.0, 8.0))
        values = settings.resolve({"site:cottage": {"heat_base_c": 9.0}}, "cottage", "large")
        self.assertEqual(heat_limits(values), HeatLimits(9.0, 9.0))

    def test_optimizer_weighs_the_whole_price_per_kwh(self):
        from unittest import mock
        from villa_kuppis import cottage_targets
        seen = []

        def fake(model, start, prices, outdoor, limits, fixed, visit=None, **kwargs):
            self.assertEqual(kwargs["heat_kw"], 2.5)
            seen.append(prices[0][1])
            return None
        with mock.patch.object(cottage_targets, "optimize_heating", fake):
            spot = self.sender.targets()["large"].price_c_kwh
            self.overrides = {"site:cottage": {"price_extra_c_kwh": 8.18}}
            self.sender.targets()
        self.assertAlmostEqual(seen[0], float(spot))
        self.assertAlmostEqual(seen[1], float(spot) + 8.18)

    def test_optimized_floor_is_never_below_the_base_level(self):
        self.overrides = {"site:cottage": {"heat_base_c": 9.0}}
        for _ in range(8):
            large = self.sender.targets()["large"]
            self.assertGreaterEqual(large.target_c, 9.0)
            self.now += timedelta(seconds=INTERVAL_SECONDS)

    def test_visit_in_a_fixed_room_keeps_the_fixed_lead(self):
        from villa_kuppis.visit import VisitStore
        visits = VisitStore(Path(self.tmp.name) / "visits.db")
        self.addCleanup(visits.close)
        self.sender._visits = visits
        self.overrides = {"site:cottage": {"heat_price_control": "fixed"}}
        visits.start(self.now + timedelta(hours=20), 20.0, ["large"], "Ville", self.now)
        self.assertNotEqual(self.sender.targets()["large"].reason, "visit")   # 12 h rule: later

    def test_visit_lead_follows_the_outdoor_forecast(self):
        from villa_kuppis.learning import Outdoor
        from villa_kuppis.visit import VisitStore
        visits = VisitStore(Path(self.tmp.name) / "visits.db")
        self.addCleanup(visits.close)
        self.sender._visits = visits
        self.overrides = {"site:cottage": {"heat_price_control": "fixed"}}
        arrival = self.now + timedelta(hours=20)
        visits.start(arrival, 20.0, ["large"], "Ville", self.now)
        forecast = lambda c: Outdoor([(int(self.now.timestamp()) + h * 3600, c)
                                      for h in range(-1, 30)])
        self.sender._models = lambda: ({}, forecast(-10.0))
        self.assertEqual(self.sender.targets()["large"].reason, "visit")   # frost: 24 h lead
        self.assertEqual(visits.active().heat_start, self.now)             # so at once

    def test_visit_lead_is_shorter_in_mild_weather(self):
        from villa_kuppis.learning import Outdoor
        from villa_kuppis.visit import VisitStore
        visits = VisitStore(Path(self.tmp.name) / "visits.db")
        self.addCleanup(visits.close)
        self.sender._visits = visits
        self.overrides = {"site:cottage": {"heat_price_control": "fixed"}}
        arrival = self.now + timedelta(hours=20)
        visits.start(arrival, 20.0, ["large"], "Ville", self.now)
        self.sender._models = lambda: ({}, Outdoor(
            [(int(self.now.timestamp()) + h * 3600, 8.0) for h in range(-1, 30)]))
        self.sender.targets()
        plan = self.sender.visit_plan
        self.assertEqual(plan.lead_hours, 8.0)
        self.assertLessEqual(plan.start, arrival - timedelta(hours=8))
        self.assertGreaterEqual(plan.start, arrival - timedelta(hours=12))

    def test_visit_rooms_keep_the_night_target_after_the_arrival(self):
        from villa_kuppis.visit import VisitStore, in_night
        visits = VisitStore(Path(self.tmp.name) / "visits.db")
        self.addCleanup(visits.close)
        self.sender._visits = visits
        arrival = self.now + timedelta(hours=14)        # 14:01 Helsinki
        visits.start(arrival, 21.0, ["large", "small"], "Ville", self.now, night_c=18.0)
        plans = self.sender.plans()
        night = ("22:00", "06:00")
        for room in ("large", "small"):     # learned and not learned
            after = [t for t in plans[room] if t.period_ts >= arrival.timestamp()]
            self.assertTrue(after)
            for t in after:
                end = datetime.fromtimestamp(t.period_ts + INTERVAL_SECONDS - 1, timezone.utc)
                with self.subTest(room=room, at=end):
                    self.assertEqual(t.target_c, 18.0 if in_night(end, night) else 21.0)
            self.assertIn(18.0, [t.target_c for t in after])
        self.overrides = {"site:cottage": {"visit_night_start": "23:00"}}
        late = [t for t in self.sender.plans()["small"]
                if t.period_ts >= self.now.timestamp() + 22 * 3600]
        self.assertEqual(late[0].target_c, 21.0)        # 22:00-22:15 now day

    def test_visit_heat_up_is_planned_with_a_slower_heater(self):
        from villa_kuppis import cottage_targets
        from villa_kuppis.visit import VisitStore
        visits = VisitStore(Path(self.tmp.name) / "visits.db")
        self.addCleanup(visits.close)
        self.sender._visits = visits
        seen = []
        real = cottage_targets.optimize_heating

        def spy(model, *args, **kwargs):
            if kwargs.get("visit"):
                seen.append(model.heat_c_per_h)
            return real(model, *args, **kwargs)
        cottage_targets.optimize_heating = spy
        self.addCleanup(setattr, cottage_targets, "optimize_heating", real)
        visits.start(self.now + timedelta(hours=20), 20.0, ["large"], "Ville", self.now)
        self.sender.targets()
        self.assertEqual(seen, [2.0 * cottage_targets.VISIT_HEAT_MARGIN])

    def test_visit_heat_up_of_a_learned_room_is_planned_to_the_arrival(self):
        from villa_kuppis.visit import VisitStore
        visits = VisitStore(Path(self.tmp.name) / "visits.db")
        self.addCleanup(visits.close)
        self.sender._visits = visits
        arrival = self.now + timedelta(hours=20)
        visits.start(arrival, 20.0, ["large", "small"], "Ville", self.now)
        targets = self.sender.targets()
        self.assertEqual(targets["large"].reason, "visit")      # the optimizer's plan
        self.assertLess(targets["large"].target_c, 20.0)        # not yet the visit target
        self.assertNotEqual(targets["small"].reason, "visit")   # 12-hour rule: later
