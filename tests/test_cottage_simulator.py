"""Stand-in cottage rooms (villa_kuppis_simulator/model.py) with the real
room controller: the simulated heaters and sensor behave like the cottage's
Mill heaters and ALPSTUGA, so the controller and Villa Kuppis can run against
cottage-haos before the devices arrive.

Both modules live in Home Assistant custom integrations without Home
Assistant imports; they are loaded here by path.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent / "custom_components"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


model = _load("cottage_sim_model", _ROOT / "villa_kuppis_simulator" / "model.py")
control = _load("cottage_control_for_sim", _ROOT / "villa_kuppis_cottage" / "control.py")

START = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def large_room(temperature=5.0, setpoint=5.0):
    # Same values as deploy/cottage/packages/villa_kuppis_simulator.yaml.
    heaters = [model.HeaterSim(f"climate.sim_mill_large_{i}", f"Mill {w} W", w, setpoint=setpoint)
               for i, w in enumerate((1050, 1050, 500), start=1)]
    return model.RoomSim("large", "Iso huone", temperature, 60.0, 0.8 * 3.6e6, heaters)


def small_room(temperature=5.0):
    heaters = [model.HeaterSim("climate.sim_mill_small_1", "Mill 1050 W", 1050)]
    return model.RoomSim("small", "Pieni huone", temperature, 22.0, 0.3 * 3.6e6, heaters)


class ModelTest(unittest.TestCase):
    def test_heater_thermostats_hold_their_setpoint_on_their_own_sensor(self):
        room = large_room(setpoint=21.0)
        model.advance(room, -10.0, 24 * 3600)
        # The heater reads 0.5 °C warm, so the room settles near 20.5 °C.
        self.assertAlmostEqual(room.temperature, 20.5, delta=0.4)

    def test_off_heaters_let_the_room_cool_toward_outdoors(self):
        room = large_room(temperature=20.0)
        for heater in room.heaters:
            heater.mode = "off"
        model.advance(room, -10.0, 12 * 3600)
        self.assertLess(room.temperature, 10.0)
        self.assertFalse(any(h.heating for h in room.heaters))

    def test_an_unreachable_heater_keeps_heating(self):
        room = small_room()
        heater = room.heaters[0]
        heater.setpoint, heater.available = 15.0, False
        model.advance(room, -5.0, 24 * 3600)
        self.assertGreater(room.temperature, 13.5)

    def test_long_outage_is_capped(self):
        room = large_room(temperature=10.0)
        model.advance(room, 0.0, 30 * 24 * 3600)   # capped at three days
        self.assertGreater(room.temperature, 4.0)

    def test_humidity_falls_when_the_room_warms(self):
        self.assertGreater(model.humidity(5.0, -5.0), model.humidity(21.0, -5.0))
        self.assertGreaterEqual(model.humidity(40.0, -25.0), 10.0)


class WithControllerTest(unittest.TestCase):
    """The room controller drives the stand-in heaters like real ones."""

    def run_room(self, room, target, hours, outdoor=-10.0):
        settings = control.Settings()
        memory = control.RoomMemory()
        goal = control.Target(target, 1, START + timedelta(hours=hours + 1))
        temps = []
        for minute in range(int(hours * 60)):
            now = START + timedelta(minutes=minute)
            sensor = control.Sensor(round(room.temperature, 1), now)
            heaters = [control.Heater(h.key, h.available, h.mode, h.setpoint,
                                      h.min_temp, h.max_temp, h.step) for h in room.heaters]
            decision = control.decide(now, goal, sensor, heaters, memory, settings)
            self.assertNotEqual(decision.mode, "manual")
            for cmd in decision.commands:
                heater = next(h for h in room.heaters if h.key == cmd.entity_id)
                if cmd.service == "set_temperature":
                    heater.setpoint = cmd.value
                else:
                    heater.mode = cmd.value
            model.advance(room, outdoor, 60)
            temps.append(room.temperature)
        return temps

    def test_large_room_heats_from_maintenance_to_a_visit_target(self):
        temps = self.run_room(large_room(), 21.0, 18)
        reached = next(i for i, t in enumerate(temps) if t >= 20.7) / 60
        self.assertLess(reached, 14)
        self.assertAlmostEqual(min(temps[-6 * 60:]), 21.0, delta=0.6)
        self.assertAlmostEqual(max(temps[-6 * 60:]), 21.0, delta=0.6)

    def test_small_room_holds_maintenance_heat(self):
        temps = self.run_room(small_room(temperature=3.0), 5.0, 12)
        self.assertGreater(min(temps[-6 * 60:]), 4.4)
        self.assertLess(max(temps[-6 * 60:]), 6.0)


if __name__ == "__main__":
    unittest.main()
