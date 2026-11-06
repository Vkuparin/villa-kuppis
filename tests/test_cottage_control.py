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
    enabled: bool = True
    own: float | None = None         # its own sensor's reading, set by the room
    step: float | None = 0.5
    holds: float | None = None       # heats to this, whatever setpoint it reports
    sticky: int = 0                  # setpoint writes it takes without holding them
    duty: float = 0.0                # recent share of time heating

    def view(self) -> control.Heater:
        return control.Heater(self.entity_id, self.available, self.mode if self.available else None,
                              self.setpoint if self.available else None, 5.0, 35.0, self.step,
                              self.enabled, current=self.own if self.available else None)


class Room:
    """One thermal node, outdoor loss and thermostat heaters."""

    BIAS = 1.5   # heater's own sensor reads this much warmer than the room
    HEAT_GAP = 0.0   # ... and this much more while it heats flat out

    def __init__(self, heaters, temp=7.0, outdoor=-10.0, ua=60.0, capacity=3.0e6, bias=None,
                 heat_gap=None):
        if bias is not None:
            self.BIAS = bias
        if heat_gap is not None:
            self.HEAT_GAP = heat_gap
        self.heaters = heaters
        for h in heaters:
            h.own = round(temp + self.BIAS, 1)
        self.temp = temp
        self.outdoor = outdoor
        self.extra_w = 0.0   # heat from elsewhere (a fireplace, the sun)
        self.ua = ua
        self.capacity = capacity

    def step(self, seconds: float) -> None:
        power = self.extra_w
        for h in self.heaters:
            own = h.own = round(self.temp + self.BIAS + self.HEAT_GAP * h.duty, 1)
            holds = h.holds if h.holds is not None else h.setpoint
            if h.mode != "heat" or not h.available:
                h.on = False
            elif own < holds - 0.25:
                h.on = True
            elif own > holds + 0.25:
                h.on = False
            h.duty += (float(h.on) - h.duty) * seconds / 1800
            power += h.watts if h.on else 0.0
        self.temp += (power - self.ua * (self.temp - self.outdoor)) * seconds / self.capacity

    def apply(self, commands) -> None:
        for cmd in commands:
            h = next(x for x in self.heaters if x.entity_id == cmd.entity_id)
            if h.ignore_commands or not h.available:
                continue
            if cmd.service == "set_temperature":
                h.setpoint = cmd.value
                if h.sticky > 0:
                    h.sticky -= 1
                else:
                    h.holds = None
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

    def test_setpoint_changes_respect_minimum_dwell(self):
        sim = Sim(large_room(temp=18.0), target=18.0)
        sim.run(6 * 60)
        sent = [(at, c.value) for at, c in sim.commands
                if c.entity_id == "climate.mill_1" and c.service == "set_temperature"]
        for (a, x), (b, y) in zip(sent, sent[1:]):
            if abs(y - x) < SETTINGS.big_change:
                self.assertGreaterEqual(b - a, SETTINGS.min_dwell)

    def test_low_target_is_raised_to_the_local_minimum(self):
        sim = Sim(large_room(temp=3.0), target=2.0)
        sim.run(60)
        self.assertEqual(sim.decisions[-1][1].effective_target, SETTINGS.safe_setpoint)
        self.assertIn(sim.decisions[-1][1].drive, ("high", "hold"))

    def test_high_target_is_capped(self):
        sim = Sim(large_room(), target=40.0)
        sim.run(1)
        self.assertEqual(sim.decisions[0][1].effective_target, SETTINGS.max_target)


class RealisticSetpointTests(unittest.TestCase):
    """Since 0.9.0 a heater gets the target corrected for its own sensor
    (5.11.2026: a Mill kept heating to a 25 °C high drive it was told to leave)."""

    @staticmethod
    def kammari(bias, target=21.0):
        room = Room([SimHeater("climate.kammari", 1000)], temp=10.0, outdoor=0.0, ua=30.0,
                    capacity=0.49e6, bias=bias)
        return Sim(room, target=target)

    def test_the_room_sensor_holds_the_target_whatever_the_heater_reads(self):
        for bias in (1.5, -2.5, -4.9):
            with self.subTest(bias=bias):
                sim = self.kammari(bias)
                sim.run(24 * 60)
                settled = sim.temps_since(START + timedelta(hours=8))
                self.assertGreater(min(settled), 20.3)
                self.assertLess(max(settled), 21.8)
                self.assertAlmostEqual(sim.memory.trim, bias, delta=0.6)
                setpoints = [c.value for _, c in sim.commands if c.service == "set_temperature"]
                # Never far from what the heater's own thermostat should hold.
                self.assertLessEqual(max(setpoints), 21.0 + bias + SETTINGS.boost + 1.0)
                self.assertLess(sum(1 for at, _ in sim.commands
                                    if at >= START + timedelta(hours=8)), 4 * 16)

    def test_a_heater_that_misses_a_command_stays_near_the_target(self):
        sim = self.kammari(-4.9, target=22.0)
        sim.run(6 * 60)
        sim.room.heaters[0].ignore_commands = True
        sim.target = control.Target(18.0, 2, sim.now + timedelta(hours=48))
        sim.run(6 * 60)
        self.assertLess(max(sim.temps_since(sim.now - timedelta(hours=5))), 23.0)

    def test_heat_up_runs_the_heaters_flat_out(self):
        sim = self.kammari(-4.9)
        sim.run(60)
        heater = sim.room.heaters[0]
        self.assertGreater(heater.setpoint, heater.own + 1.0)
        self.assertEqual(sim.decisions[-1][1].drive, "high")

    def test_a_heater_too_small_for_the_target_does_not_wind_up_the_trim(self):
        # 5.-6.11.2026: Tienpuoli alone (two Mills unplugged) ran flat out all
        # night 0.5 °C under a 12 °C target; the trim grew to 7.5 and after the
        # target fell to 8 °C it kept heating a 9 °C room at a 14.5 °C setpoint.
        room = Room([SimHeater("climate.tienpuoli", 690, step=0.1)], temp=11.0, outdoor=0.0,
                    ua=60.0, capacity=3.0e6, bias=0.8)
        sim = Sim(room, target=12.0)
        sim.run(15 * 60)
        self.assertAlmostEqual(sim.memory.trim, 0.8, delta=1.0)
        sim.target = control.Target(8.0, 2, sim.now + timedelta(hours=48))
        drop = sim.now
        sim.run(10 * 60)
        heater = room.heaters[0]
        for at, cmd in sim.commands:
            if at >= drop and cmd.service == "set_temperature":
                # While the room is warm, never a setpoint the heater would heat to.
                temp = next(t for when, _, t in sim.decisions if when >= at)
                if temp > 8.5:
                    self.assertLess(cmd.value, temp + room.BIAS, (at, cmd.value, temp))
        settled = sim.temps_since(drop + timedelta(hours=8))
        self.assertLess(max(settled), 8.9)
        self.assertGreater(min(settled), 7.3)
        self.assertLess(heater.setpoint, 12.0)

    def test_the_trim_recovers_from_a_misleading_start(self):
        # 5.11.2026: Kammari had heated to 25 °C, the Mill read 5.5 °C under
        # the ALPSTUGA while the room cooled, and the trim started there.
        # Held at its setpoint the heater reads 2.5 °C under the room.
        room = Room([SimHeater("climate.kammari", 1000, step=None)], temp=23.6, outdoor=0.0,
                    ua=30.0, capacity=0.49e6, bias=-2.5)
        room.heaters[0].own = 18.1
        sim = Sim(room, target=18.0)
        sim.run(24 * 60)
        self.assertAlmostEqual(sim.memory.trim, -2.5, delta=0.8)
        settled = sim.temps_since(START + timedelta(hours=14))
        self.assertGreater(min(settled), 17.3)
        self.assertLess(max(settled), 18.8)

    def test_a_gap_that_grows_while_heating_still_holds_the_target(self):
        room = Room([SimHeater("climate.kammari", 1000, step=None)], temp=10.0, outdoor=0.0,
                    ua=30.0, capacity=0.49e6, bias=-1.0, heat_gap=-3.0)
        sim = Sim(room, target=21.0)
        sim.run(24 * 60)
        settled = sim.temps_since(START + timedelta(hours=10))
        self.assertGreater(min(settled), 20.2)
        self.assertLess(max(settled), 21.9)

    def test_whole_degrees_for_a_heater_without_a_step(self):
        heater = control.Heater("climate.mill", True, "heat", 20.0, 5.0, 35.0, None,
                                current=17.3)
        decision = control.decide(START, control.Target(18.0, 1, START + timedelta(hours=1)),
                                  control.Sensor(19.6, START), [heater], control.RoomMemory())
        value = next(c.value for c in decision.commands if c.service == "set_temperature")
        self.assertEqual(value, round(value))


class RunawayTests(unittest.TestCase):
    """A heater that reports one setpoint and heats to another (5.11.2026)."""

    @staticmethod
    def warm_kammari(sticky):
        room = Room([SimHeater("climate.kammari", 1000, step=None)], temp=21.0, outdoor=0.0,
                    ua=30.0, capacity=0.49e6, bias=-2.5)
        heater = room.heaters[0]
        heater.setpoint, heater.holds, heater.sticky = 18.0, 25.0, sticky
        return Sim(room, target=18.0)

    def test_its_setpoint_is_sent_again_and_then_holds(self):
        sim = self.warm_kammari(sticky=1)   # the first write does not take
        sim.run(4 * 60)
        setpoints = [c.value for _, c in sim.commands if c.service == "set_temperature"]
        self.assertGreaterEqual(len(setpoints), 2)
        self.assertIsNone(sim.room.heaters[0].holds)
        temps = sim.temps_since(START)
        self.assertLess(max(temps), 23.5)
        self.assertLess(temps[-1], max(temps) - 1.0)

    def test_the_status_says_when_it_keeps_warming(self):
        sim = self.warm_kammari(sticky=99)
        sim.run(4 * 60)
        self.assertIn("lämpenee yli asetuksensa", sim.decisions[-1][1].reason)
        # A resend, then off and on with the setpoint, once per window.
        sends = sorted({at for at, _ in sim.commands})
        self.assertLessEqual(max(sum(1 for t in sends if s <= t < s + timedelta(hours=1))
                                 for s in sends), 3)
        self.assertIn(("set_hvac_mode", "off"),
                      [(c.service, c.value) for _, c in sim.commands])
        self.assertNotIn("manual", sim.modes_since(START))
        # Warm, not cold: no phone alert.
        self.assertFalse(control.disobeying(sim.room.heaters[0].view(), sim.memory))

    def test_warmth_from_elsewhere_costs_only_a_resend_now_and_then(self):
        room = Room([SimHeater("climate.kammari", 1000, step=None)], temp=19.0, outdoor=0.0,
                    ua=30.0, capacity=0.49e6, bias=-2.5)
        sim = Sim(room, target=18.0)
        sim.run(60)
        room.extra_w = 1000.0            # a fireplace
        sim.run(4 * 60)
        self.assertGreater(room.temp, 24.0)
        late = {at for at, _ in sim.commands if at >= START + timedelta(hours=1)}
        self.assertLessEqual(len(late), 12)
        self.assertNotIn("manual", sim.modes_since(START))
        self.assertFalse(control.disobeying(room.heaters[0].view(), sim.memory))

    def test_a_heater_that_does_not_heat_is_retried_and_then_reported(self):
        # 5.11.2026: the Mill took 12, 13 and 19 °C and kept 11 °C.
        room = Room([SimHeater("climate.kammari", 1000, step=None)], temp=17.0, outdoor=0.0,
                    ua=30.0, capacity=0.49e6, bias=-2.5)
        heater = room.heaters[0]
        heater.setpoint, heater.holds, heater.sticky = 11.0, 11.0, 99
        sim = Sim(room, target=21.0)
        sim.run(30)
        self.assertFalse(control.disobeying(heater.view(), sim.memory))
        sim.run(2 * 60)
        self.assertTrue(control.disobeying(heater.view(), sim.memory))
        self.assertIn("ei lämmitä asetuksensa", sim.decisions[-1][1].reason)
        self.assertNotIn("manual", sim.modes_since(START))

    def test_a_restart_that_helps_ends_the_report(self):
        room = Room([SimHeater("climate.kammari", 1000, step=None)], temp=17.0, outdoor=0.0,
                    ua=30.0, capacity=0.49e6, bias=-2.5)
        heater = room.heaters[0]
        heater.setpoint, heater.holds, heater.sticky = 11.0, 11.0, 1   # the second write takes
        sim = Sim(room, target=21.0)
        sim.run(3 * 60)
        self.assertIsNone(heater.holds)
        self.assertFalse(control.disobeying(heater.view(), sim.memory))
        self.assertGreater(room.temp, 19.0)


class WatchdogTests(unittest.TestCase):
    def test_expired_target_falls_back_to_the_safe_setpoint(self):
        sim = Sim(large_room(), target=20.0, valid_hours=6)
        sim.run(18 * 60)
        self.assertEqual(sim.decisions[-1][1].mode, "fallback")
        self.assertEqual(sim.decisions[-1][1].reason, "tavoite vanheni")
        self.assertTrue(all(h.setpoint == SETTINGS.safe_setpoint for h in sim.room.heaters))
        # The heaters' thermostats hold the room near the safe setpoint.
        late = sim.temps_since(START + timedelta(hours=16))
        self.assertGreater(min(late), SETTINGS.safe_setpoint - Room.BIAS - 1.0)
        self.assertLess(max(late), 12.0)

    def test_schedule_keeps_the_plan_through_an_outage_then_falls_back(self):
        # Card H3.3: the last write carried the plan; nothing arrives after it.
        sim = Sim(large_room(temp=8.0), target=8.0, valid_hours=0.75)
        sim.target = replace(sim.target, valid_until=START + timedelta(hours=6), schedule=(
            (START + timedelta(hours=1), 16.0), (START + timedelta(hours=5, minutes=45), 10.0)))
        sim.run(5 * 60)
        self.assertEqual(sim.decisions[59][1].effective_target, 8.0)
        self.assertEqual(sim.decisions[60][1].effective_target, 16.0)
        self.assertEqual(sim.modes_since(START + timedelta(hours=1)), {"follow"})
        self.assertGreater(sim.room.temp, 12.0)   # heating towards 16 °C all along
        sim.run(90)
        self.assertEqual(sim.decisions[5 * 60 + 45][1].effective_target, 10.0)
        self.assertEqual(sim.decisions[-1][1].mode, "fallback")
        self.assertEqual(sim.decisions[-1][1].reason, "tavoite vanheni")

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
        self.assertIn("vanhentunut", sim.decisions[-1][1].reason)
        # The target, corrected for the heaters' own sensors as learned.
        expected = round((16.0 + sim.memory.trim) * 2) / 2
        self.assertTrue(all(h.setpoint == expected for h in sim.room.heaters))

    def test_implausible_sensor_value_is_not_followed(self):
        sim = Sim(large_room(), target=16.0)
        sim.sensor_override = control.Sensor(85.0, START)
        sim.run(2)
        self.assertEqual(sim.decisions[-1][1].mode, "thermostat")
        self.assertIn("epäuskottava", sim.decisions[-1][1].reason)

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
        self.assertEqual(sim.decisions[-1][1].effective_target, 24.0)
        self.assertEqual(len(sim.commands), sent_before)
        self.assertEqual(sim.room.heaters[0].setpoint, 24.0)
        sim.run(int(SETTINGS.manual_hold.total_seconds() // 60))
        self.assertEqual(sim.decisions[-1][1].mode, "follow")
        resumed = [at for at, _ in sim.commands[sent_before:]]
        self.assertGreaterEqual(resumed[0] - changed_at, SETTINGS.manual_hold)

    def test_heater_switched_off_by_hand_is_turned_back_on_after_the_hold(self):
        sim = Sim(large_room(), target=18.0)
        sim.run(60)
        sim.room.heaters[2].mode = "off"
        sim.run(30)
        self.assertEqual(sim.decisions[-1][1].mode, "manual")
        sim.run(int(SETTINGS.manual_hold.total_seconds() // 60))
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
        self.assertIn(heater.setpoint, {c.value for _, c in sim.commands
                                        if c.entity_id == heater.entity_id})


    def test_the_mill_app_program_taking_over_is_undone_not_a_hand_change(self):
        # Mill's local connection reports its weekly program as "auto": a
        # program in the Mill app or its cloud must not replace Villa Kuppis.
        sim = Sim(large_room(), target=18.0)
        sim.run(3 * 60)
        heater = sim.room.heaters[0]
        heater.mode, heater.setpoint = "auto", 22.0
        changed_at, sent_before = sim.now, len(sim.commands)
        sim.run(10)
        self.assertNotIn("manual", sim.modes_since(changed_at))
        self.assertEqual(heater.mode, "heat")
        sent = [(c.service, c.value) for _, c in sim.commands[sent_before:]
                if c.entity_id == heater.entity_id]
        self.assertIn(("set_hvac_mode", "heat"), sent)
        self.assertIn("set_temperature", {service for service, _ in sent})
        self.assertNotEqual(heater.setpoint, 22.0)

    def test_hand_change_holds_overnight(self):
        self.assertGreaterEqual(SETTINGS.manual_hold, timedelta(hours=12))

    def test_heater_back_online_with_a_cached_then_own_setpoint_is_not_manual(self):
        # 4.11.2026: a Mill plugged in at the cottage showed HA's cached 5 °C,
        # then its own higher setpoint, and got a 4 h "hand change".
        sim = Sim(large_room(), target=None)
        sim.run(10)
        heater = sim.room.heaters[0]
        heater.available = False
        sim.run(30)
        heater.available = True
        heater.setpoint = SETTINGS.safe_setpoint   # cached state first
        sim.run(1)
        heater.setpoint = 21.0                     # then the Mill's own
        mark = len(sim.commands)
        sim.run(5)
        self.assertNotIn("manual", sim.modes_since(START))
        self.assertIn((heater.entity_id, SETTINGS.safe_setpoint),
                      [(c.entity_id, c.value) for _, c in sim.commands[mark:]])
        self.assertEqual(heater.setpoint, SETTINGS.safe_setpoint)

    def test_hand_change_after_the_reconnect_settle_is_manual(self):
        sim = Sim(large_room(), target=18.0)
        sim.run(10)
        heater = sim.room.heaters[0]
        heater.available = False
        sim.run(5)
        heater.available = True
        sim.run(int(SETTINGS.reconnect_settle.total_seconds() // 60) + 5)
        self.assertNotIn("manual", sim.modes_since(START))
        heater.setpoint = 24.0
        sim.run(2)
        self.assertEqual(sim.decisions[-1][1].mode, "manual")


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


class SwitchedOffHeaterTests(unittest.TestCase):
    def test_switched_off_heater_stays_off_and_returns_when_switched_on(self):
        sim = Sim(large_room(), target=18.0)
        heater = sim.room.heaters[2]
        sim.run(60)
        heater.enabled = False
        sim.run(60)
        self.assertEqual(heater.mode, "off")
        self.assertNotIn("manual", sim.modes_since(START))
        off_cmds = [c for _, c in sim.commands if c.entity_id == heater.entity_id
                    and c.service == "set_hvac_mode" and c.value == "off"]
        self.assertEqual(len(off_cmds), 1)
        heater.mode = "heat"   # someone turns it on at the cottage
        sim.run(10)
        self.assertEqual(heater.mode, "off")
        self.assertNotIn("manual", sim.modes_since(START))
        heater.enabled = True
        sim.run(10)
        self.assertEqual(heater.mode, "heat")
        self.assertNotIn("manual", sim.modes_since(START))

    def test_fallback_heats_a_switched_off_heater_unless_configured_not_to(self):
        for heat_all, expected in ((True, "heat"), (False, "off")):
            settings = replace(SETTINGS, fallback_heats_disabled=heat_all)
            sim = Sim(large_room(), target=18.0, valid_hours=1, settings=settings)
            heater = sim.room.heaters[0]
            heater.enabled = False
            sim.run(30)
            self.assertEqual(heater.mode, "off")
            sim.run(2 * 60)
            self.assertEqual(sim.decisions[-1][1].mode, "fallback")
            self.assertEqual(heater.mode, expected, heat_all)
            if heat_all:
                self.assertEqual(heater.setpoint, SETTINGS.safe_setpoint)


class FrostGuardTests(unittest.TestCase):
    def test_switched_off_only_heater_still_keeps_the_room_from_freezing(self):
        heater = SimHeater("climate.kammari", 1000, setpoint=5.0)
        sim = Sim(Room([heater], temp=8.0, outdoor=-10.0), target=5.0)
        heater.enabled = False
        sim.run(30)
        self.assertEqual(heater.mode, "off")     # warm enough: the switch is obeyed
        sim.run(12 * 60)
        self.assertEqual(heater.mode, "heat")
        self.assertEqual(heater.setpoint, SETTINGS.safe_setpoint)
        self.assertGreater(min(sim.temps_since(START + timedelta(hours=6))), 3.0)
        self.assertIn("pakkasvahti", sim.decisions[-1][1].reason)
        self.assertNotIn("manual", sim.modes_since(START))
        sim.room.temp = 15.0                     # warmed by something else
        sim.run(10)
        self.assertEqual(heater.mode, "off")

    def test_switched_off_heater_holds_the_safe_setpoint_without_a_room_reading(self):
        heater = SimHeater("climate.kammari", 1000, setpoint=5.0)
        sim = Sim(Room([heater], temp=12.0), target=8.0)
        heater.enabled = False
        sim.run(10)
        self.assertEqual(heater.mode, "off")
        sim.sensor_override = control.Sensor(None, None)
        sim.run(10)
        self.assertEqual(sim.decisions[-1][1].mode, "thermostat")
        self.assertEqual((heater.mode, heater.setpoint), ("heat", SETTINGS.safe_setpoint))

    def test_manual_hold_does_not_let_the_room_freeze(self):
        heater = SimHeater("climate.kammari", 1000, setpoint=5.0)
        sim = Sim(Room([heater], temp=7.0, outdoor=-15.0), target=6.0)
        sim.run(30)
        heater.mode = "off"                      # turned off from Google Home
        sim.run(4 * 60 - 40)
        self.assertEqual(sim.decisions[-1][1].mode, "manual")
        self.assertEqual(heater.mode, "heat")
        self.assertGreaterEqual(heater.setpoint, SETTINGS.safe_setpoint)
        self.assertIn("pakkasvahti", sim.decisions[-1][1].reason)
        self.assertGreater(min(sim.temps_since(START + timedelta(hours=2))), 3.0)

    def test_frost_guard_never_turns_a_persons_setpoint_down(self):
        heater = SimHeater("climate.kammari", 1000, setpoint=5.0)
        sim = Sim(Room([heater], temp=3.0, outdoor=-15.0), target=5.0)
        sim.run(5)
        heater.setpoint = 21.0                   # someone arrives and turns it up
        mark = len(sim.commands)
        sim.run(30)
        self.assertEqual(sim.decisions[-1][1].mode, "manual")
        self.assertEqual(heater.setpoint, 21.0)
        self.assertEqual(sim.commands[mark:], [])


class DeviceFitTests(unittest.TestCase):
    def test_heater_rounding_the_setpoint_is_not_a_manual_change(self):
        sim = Sim(large_room(temp=6.0), target=7.5)
        original = sim.room.apply

        def whole_degrees(commands):
            original([replace(c, value=float(round(c.value)))
                      if c.service == "set_temperature" else c for c in commands])
        sim.room.apply = whole_degrees
        sim.run(3 * 60)
        self.assertNotIn("manual", sim.modes_since(START))
        self.assertLessEqual(sim.max_commands_per_hour(), 9)

    def test_switching_off_lowers_the_setpoint_before_off(self):
        sim = Sim(large_room(temp=6.0), target=18.0)
        heater = sim.room.heaters[0]
        sim.run(10)
        self.assertEqual(heater.setpoint, 18.0 + Room.BIAS + SETTINGS.boost)
        heater.enabled = False
        mark = len(sim.commands)
        sim.run(5)
        sent = [(c.service, c.value) for _, c in sim.commands[mark:]
                if c.entity_id == heater.entity_id]
        self.assertEqual(sent[:2], [("set_temperature", SETTINGS.safe_setpoint),
                                    ("set_hvac_mode", "off")])

    def test_status_names_a_heater_that_keeps_ignoring_commands(self):
        sim = Sim(large_room(temp=10.0), target=18.0)
        sim.room.heaters[0].ignore_commands = True
        sim.run(30)
        self.assertIn("climate.mill_1 ei ota lämpötilaa 22.5 °C", sim.decisions[-1][1].reason)
        self.assertNotIn("mill_2", sim.decisions[-1][1].reason)


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


_ALARM_PATH = _PATH.with_name("alarm.py")
_alarm_spec = importlib.util.spec_from_file_location("cottage_alarm", _ALARM_PATH)
cottage_alarm = importlib.util.module_from_spec(_alarm_spec)
sys.modules[_alarm_spec.name] = cottage_alarm
_alarm_spec.loader.exec_module(cottage_alarm)


class CottageAlarmTests(unittest.TestCase):
    """The cottage's own alarm (alarm.py) when Villa Kuppis is silent."""

    def titles(self, alarm, minutes, rooms, delivered=True, plan_until=None):
        """Titles due then; each counts as said when ``delivered``."""
        out = []
        for key, title, _message in alarm.check(START + timedelta(minutes=minutes), rooms,
                                                5.0, plan_until):
            if delivered:
                alarm.sent(key)
            out.append(title)
        return out

    def test_a_cold_room_is_reported_once_and_over_a_degree_above(self):
        alarm = cottage_alarm.Alarm()
        alarm.heartbeat(START)
        self.assertEqual(self.titles(alarm, 30, {"Kammari": 2.0}), [])
        self.assertEqual(self.titles(alarm, 61, {"Kammari": 2.0, "Mökki": None}),
                         ["Mökki: Villa Kuppis ei vastaa", "Mökki: Kammari on kylmä"])
        self.assertEqual(self.titles(alarm, 70, {"Kammari": 3.5}), [])
        self.assertEqual(self.titles(alarm, 80, {"Kammari": None}), [])
        self.assertEqual(self.titles(alarm, 90, {"Kammari": 4.0}), ["Mökki: Kammari on taas lämpimämpi"])

    def test_no_silence_notice_right_after_the_cottage_starts(self):
        # 4.11.2026: the cottage HA came back after two days off; the last
        # heartbeat was old, so the alarm went off 3 s after the start.
        alarm = cottage_alarm.Alarm()
        alarm.heartbeat(START - timedelta(days=2))
        alarm.started = START
        self.assertEqual(self.titles(alarm, 1, {"Kammari": 2.0}), [])
        alarm.heartbeat(START + timedelta(minutes=5))
        self.assertEqual(self.titles(alarm, 11, {"Kammari": 2.0}), [])

    def test_silence_after_the_start_grace_is_still_reported(self):
        alarm = cottage_alarm.Alarm()
        alarm.heartbeat(START - timedelta(days=2))
        alarm.started = START
        self.assertEqual(self.titles(alarm, 9, {}), [])
        self.assertEqual(self.titles(alarm, 11, {}), ["Mökki: Villa Kuppis ei vastaa"])

    def test_nothing_before_the_first_heartbeat(self):
        alarm = cottage_alarm.Alarm()
        self.assertEqual(self.titles(alarm, 600, {"Mökki": 1.0}), [])

    def test_a_notice_no_phone_got_is_offered_again(self):
        alarm = cottage_alarm.Alarm()
        alarm.heartbeat(START)
        both = ["Mökki: Villa Kuppis ei vastaa", "Mökki: Kammari on kylmä"]
        self.assertEqual(self.titles(alarm, 61, {"Kammari": 2.0}, delivered=False), both)
        self.assertEqual(self.titles(alarm, 62, {"Kammari": 2.0}), both)
        self.assertEqual(self.titles(alarm, 63, {"Kammari": 2.0}), [])
        alarm.heartbeat(START + timedelta(minutes=64))
        self.assertEqual(self.titles(alarm, 64, {"Kammari": 2.0}, delivered=False),
                         ["Mökki: Villa Kuppis vastaa taas"])
        self.assertEqual(self.titles(alarm, 65, {"Kammari": 2.0}),
                         ["Mökki: Villa Kuppis vastaa taas"])

    def test_the_silence_notice_says_how_long_the_last_plan_lasts(self):
        alarm = cottage_alarm.Alarm()
        alarm.heartbeat(START)
        now = START + timedelta(minutes=61)
        until = datetime(2026, 11, 21, 16, 0, tzinfo=timezone.utc)   # 18.00 in Helsinki
        message = alarm.check(now, {}, 5.0, until)[0][2]
        self.assertIn("seuraa viimeisintä suunnitelmaa 21.11. klo 18.00 asti", message)
        self.assertIn("turvalämmön 5 °C", message)
        message = alarm.check(now, {}, 5.0, None)[0][2]
        self.assertIn("Patterit pitävät turvalämmön 5 °C.", message)
