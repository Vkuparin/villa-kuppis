"""Offline simulations of the cottage room controller (card H2.2).

The controller module lives in the Home Assistant custom integration and has
no Home Assistant imports; it is loaded here by path. A one-node room model
with thermostat heaters stands in for the cottage: the heaters' own sensors
read warmer than the room sensor, like a wall heater's built-in thermostat.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

_PATH = (Path(__file__).resolve().parent.parent / "custom_components"
         / "villa_kuppis_cottage" / "control.py")
_spec = importlib.util.spec_from_file_location("cottage_control", _PATH)
control = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = control
_spec.loader.exec_module(control)

START = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
SETTINGS = control.Settings()


@dataclass
class SimHeater:
    entity_id: str
    watts: float
    mode: str = "heat"
    setpoint: float = 7.0
    on: bool = False
    available: bool = True
    ignore_commands: bool = False

    def view(self) -> control.Heater:
        return control.Heater(self.entity_id, self.available, self.mode if self.available else None,
                              self.setpoint if self.available else None, 5.0, 35.0, 0.5)


class Room:
    """One thermal node, outdoor loss and thermostat heaters."""

    BIAS = 1.5   # heater's own sensor reads this much warmer than the room

    def __init__(self, heaters, temp=7.0, outdoor=-10.0, ua=60.0, capacity=3.0e6):
        self.heaters = heaters
        self.temp = temp
        self.outdoor = outdoor
        self.ua = ua
        self.capacity = capacity

    def step(self, seconds: float) -> None:
        power = 0.0
        for h in self.heaters:
            own = self.temp + self.BIAS
            if h.mode != "heat" or not h.available:
                h.on = False
            elif own < h.setpoint - 0.25:
                h.on = True
            elif own > h.setpoint + 0.25:
                h.on = False
            power += h.watts if h.on else 0.0
        self.temp += (power - self.ua * (self.temp - self.outdoor)) * seconds / self.capacity

    def apply(self, commands) -> None:
        for cmd in commands:
            h = next(x for x in self.heaters if x.entity_id == cmd.entity_id)
            if h.ignore_commands or not h.available:
                continue
            if cmd.service == "set_temperature":
                h.setpoint = cmd.value
            elif cmd.service == "set_hvac_mode":
                h.mode = cmd.value


class Sim:
    def __init__(self, room: Room, target: float | None = 18.0, *, valid_hours: float = 48,
                 settings=SETTINGS):
        self.room = room
        self.now = START
        self.memory = control.RoomMemory()
        self.settings = settings
        self.target = (control.Target(target, 1, START + timedelta(hours=valid_hours))
                       if target is not None else None)
        self.sensor_ok = True
        self.sensor_override = None
        self.sensor_reported = START
        self.decisions = []
        self.commands = []

    def sensor(self):
        if self.sensor_override is not None:
            return self.sensor_override
        if self.sensor_ok:
            self.sensor_reported = self.now
        return control.Sensor(round(self.room.temp, 1), self.sensor_reported)

    def run(self, minutes: int) -> None:
        for _ in range(minutes):
            decision = control.decide(self.now, self.target, self.sensor(),
                                      [h.view() for h in self.room.heaters],
                                      self.memory, self.settings)
            self.decisions.append((self.now, decision, self.room.temp))
            self.commands.extend((self.now, c) for c in decision.commands)
            self.room.apply(decision.commands)
            for _ in range(2):
                self.room.step(30)
            self.now += timedelta(minutes=1)

    def temps_since(self, start: datetime):
        return [t for at, _, t in self.decisions if at >= start]

    def modes_since(self, start: datetime):
        return {d.mode for at, d, _ in self.decisions if at >= start}

    def max_commands_per_hour(self) -> int:
        times = [at for at, _ in self.commands]
        return max((sum(1 for t in times if s <= t < s + timedelta(hours=1)) for s in times), default=0)


def large_room(**kwargs) -> Room:
    heaters = [SimHeater("climate.mill_1", 1050), SimHeater("climate.mill_2", 1050),
               SimHeater("climate.mill_3", 500)]
    return Room(heaters, **kwargs)


class FollowTests(unittest.TestCase):
    def test_reaches_and_holds_the_target_with_few_commands(self):
        sim = Sim(large_room(), target=18.0)
        sim.run(24 * 60)
        settled = sim.temps_since(START + timedelta(hours=12))
        self.assertGreater(min(settled), 17.0)
        self.assertLess(max(settled), 19.0)
        self.assertEqual(sim.modes_since(START), {"follow"})
        self.assertLessEqual(sim.max_commands_per_hour(), 3 * 6)
        per_heater = sum(1 for _, c in sim.commands if c.entity_id == "climate.mill_1")
        self.assertLess(per_heater, 24 * 6)

    def test_drive_respects_minimum_dwell(self):
        sim = Sim(large_room(temp=18.0), target=18.0)
        sim.run(6 * 60)
        switches = [at for (at, d, _), (_, prev, _) in zip(sim.decisions[1:], sim.decisions)
                    if d.drive != prev.drive]
        for a, b in zip(switches, switches[1:]):
            self.assertGreaterEqual(b - a, SETTINGS.min_dwell)

    def test_low_target_is_raised_to_the_local_minimum(self):
        sim = Sim(large_room(temp=3.0), target=2.0)
        sim.run(60)
        self.assertEqual(sim.decisions[-1][1].effective_target, SETTINGS.safe_setpoint)
        self.assertEqual(sim.decisions[-1][1].drive, "high")

    def test_high_target_is_capped(self):
        sim = Sim(large_room(), target=40.0)
        sim.run(1)
        self.assertEqual(sim.decisions[0][1].effective_target, SETTINGS.max_target)


class WatchdogTests(unittest.TestCase):
    def test_expired_target_falls_back_to_the_safe_setpoint(self):
        sim = Sim(large_room(), target=20.0, valid_hours=6)
        sim.run(18 * 60)
        self.assertEqual(sim.decisions[-1][1].mode, "fallback")
        self.assertEqual(sim.decisions[-1][1].reason, "the target expired")
        self.assertTrue(all(h.setpoint == SETTINGS.safe_setpoint for h in sim.room.heaters))
        # The heaters' thermostats hold the room near the safe setpoint.
        late = sim.temps_since(START + timedelta(hours=16))
        self.assertGreater(min(late), SETTINGS.safe_setpoint - Room.BIAS - 1.0)
        self.assertLess(max(late), 12.0)

    def test_no_target_since_start_is_fallback(self):
        sim = Sim(large_room(), target=None)
        for h in sim.room.heaters:
            h.setpoint = 12.0
        sim.run(5)
        self.assertEqual(sim.decisions[0][1].mode, "fallback")
        self.assertEqual({c.value for _, c in sim.commands}, {SETTINGS.safe_setpoint})

    def test_stale_sensor_hands_the_target_to_the_heater_thermostats(self):
        sim = Sim(large_room(), target=16.0)
        sim.run(8 * 60)
        sim.sensor_ok = False
        sim.run(60)
        self.assertEqual(sim.decisions[-1][1].mode, "thermostat")
        self.assertIn("stale", sim.decisions[-1][1].reason)
        self.assertTrue(all(h.setpoint == 16.0 for h in sim.room.heaters))

    def test_implausible_sensor_value_is_not_followed(self):
        sim = Sim(large_room(), target=16.0)
        sim.sensor_override = control.Sensor(85.0, START)
        sim.run(2)
        self.assertEqual(sim.decisions[-1][1].mode, "thermostat")
        self.assertIn("implausible", sim.decisions[-1][1].reason)

    def test_restart_with_empty_memory_is_not_a_manual_change(self):
        sim = Sim(large_room(), target=18.0)
        sim.run(10 * 60)
        sim.memory = control.RoomMemory()   # Home Assistant restarted
        sim.run(60)
        self.assertNotIn("manual", sim.modes_since(START))


class ManualTests(unittest.TestCase):
    def test_hand_change_holds_the_room_then_resumes(self):
        sim = Sim(large_room(), target=18.0)
        sim.run(3 * 60)
        sim.room.heaters[0].setpoint = 24.0    # someone uses Google Home
        changed_at = sim.now
        sent_before = len(sim.commands)
        sim.run(60)
        self.assertEqual(sim.decisions[-1][1].mode, "manual")
        self.assertEqual(len(sim.commands), sent_before)
        self.assertEqual(sim.room.heaters[0].setpoint, 24.0)
        sim.run(4 * 60)
        self.assertEqual(sim.decisions[-1][1].mode, "follow")
        resumed = [at for at, _ in sim.commands[sent_before:]]
        self.assertGreaterEqual(resumed[0] - changed_at, SETTINGS.manual_hold)

    def test_heater_switched_off_by_hand_is_turned_back_on_after_the_hold(self):
        sim = Sim(large_room(), target=18.0)
        sim.run(60)
        sim.room.heaters[2].mode = "off"
        sim.run(30)
        self.assertEqual(sim.decisions[-1][1].mode, "manual")
        sim.run(4 * 60)
        self.assertEqual(sim.room.heaters[2].mode, "heat")

    def test_own_commands_with_delayed_read_back_are_not_manual(self):
        sim = Sim(large_room(), target=18.0)
        heater = sim.room.heaters[0]
        original = sim.room.apply

        def slow_apply(commands):
            # The heater reports the new setpoint one tick later.
            sim.pending = getattr(sim, "pending", [])
            original(sim.pending)
            sim.pending = list(commands)
        sim.room.apply = slow_apply
        sim.run(6 * 60)
        self.assertNotIn("manual", sim.modes_since(START))
        self.assertIn(heater.setpoint, (SETTINGS.low_setpoint, 21.0))


class CommandRateTests(unittest.TestCase):
    def test_ignored_commands_are_retried_within_the_hourly_limit(self):
        sim = Sim(large_room(), target=18.0)
        sim.room.heaters[0].ignore_commands = True
        sim.run(3 * 60)
        to_first = [at for at, c in sim.commands if c.entity_id == "climate.mill_1"]
        self.assertGreater(len(to_first), 1)
        for a, b in zip(to_first, to_first[1:]):
            self.assertGreaterEqual(b - a, SETTINGS.confirm_grace)
        per_hour = [sum(1 for t in to_first if s <= t < s + timedelta(hours=1)) for s in to_first]
        self.assertLessEqual(max(per_hour), SETTINGS.max_commands_per_hour)

    def test_unavailable_heater_gets_no_commands_and_returns_as_a_baseline(self):
        sim = Sim(large_room(), target=18.0)
        sim.run(30)
        heater = sim.room.heaters[1]
        heater.available = False
        mark = len(sim.commands)
        sim.run(30)
        self.assertFalse(any(c.entity_id == heater.entity_id for _, c in sim.commands[mark:]))
        heater.available = True
        heater.setpoint = 10.0   # came back from a power cut with another value
        sim.run(5)
        self.assertNotIn("manual", sim.modes_since(START))
        self.assertTrue(any(c.entity_id == heater.entity_id for _, c in sim.commands[mark:]))


class SetpointTests(unittest.TestCase):
    def test_clamp_and_round_to_the_heater_step(self):
        h = control.Heater("climate.x", True, "heat", 10.0, 5.0, 35.0, 0.5)
        self.assertEqual(control.heater_setpoint(h, 21.3, SETTINGS), 21.5)
        self.assertEqual(control.heater_setpoint(h, 2.0, SETTINGS), 5.0)
        self.assertEqual(control.heater_setpoint(h, 50.0, SETTINGS), 35.0)
        coarse = replace(h, step=1.0, min_temp=5.0, max_temp=30.0)
        self.assertEqual(control.heater_setpoint(coarse, 29.7, SETTINGS), 30.0)


if __name__ == "__main__":
    unittest.main()
