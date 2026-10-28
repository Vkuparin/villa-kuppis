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
            prices_db=data / "prices.db", overrides=lambda: self.overrides,
            automation_on=lambda: self.automation, now=self.clock)

    async def asyncTearDown(self):
        self.gateway.request_stop()
        await asyncio.wait_for(self.task, 3.0)
        await self.ha.stop()
        self.bindings.close()
        self.tmp.cleanup()

    async def settle(self, sent):
        for cmd in sent:
            if cmd["state"] == "pending":
                await self.gateway.wait(cmd["request_id"], 3.0)

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

    async def test_automation_off_sends_nothing(self):
        self.automation = False
        self.assertEqual(self.sender.tick(), [])
        self.assertEqual(self.ha.calls, [])

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
        targets = current_targets({}, periods, ["large"], 900 + 899)
        self.assertEqual(targets["large"].target_c, 8.0)
        self.assertEqual(targets["large"].period_ts, 900)
        self.assertEqual(targets["large"].reason, "threshold")


class OptimizedTargetTests(unittest.TestCase):
    """Card F5.2: a learned room takes the heating optimizer's target."""

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
        self.calls = 0
        model = RoomModel(True, drift_c_per_h=-0.5, heat_c_per_h=2.0)

        def models():
            self.calls += 1
            return {"large": model}, Outdoor([])
        self.sender = CottageTargetSender(
            None, {"large": "large-status", "small": "small-status"},
            prices_db=data / "prices.db", overrides=lambda: self.overrides,
            automation_on=lambda: True, now=lambda: self.now,
            models=models, room_temps=lambda: self.temps)

    def test_learned_room_follows_the_optimizer_within_its_band(self):
        reasons = set()
        for _ in range(8):
            targets = self.sender.targets()
            self.assertIn(targets["small"].reason, ("base", "threshold"))   # not learned
            large = targets["large"]
            reasons.add(large.reason)
            if large.reason == "optimized":
                self.assertGreaterEqual(large.target_c, 5.0)
                self.assertLessEqual(large.target_c, 12.0)
            self.now += timedelta(seconds=INTERVAL_SECONDS)
        self.assertEqual(reasons, {"optimized"})
        # Planned once per period, not on every call.
        calls = self.calls
        self.sender.targets()
        self.sender.targets()
        self.assertEqual(self.calls, calls + 1)

    def test_fixed_setting_or_unknown_temperature_keep_the_thresholds(self):
        self.overrides = {"site:cottage": {"heat_price_control": "fixed"}}
        self.assertEqual(self.sender.targets()["large"].reason, "threshold")
        self.overrides = {}
        self.temps = {}
        self.assertEqual(self.sender.targets()["large"].reason, "threshold")

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
