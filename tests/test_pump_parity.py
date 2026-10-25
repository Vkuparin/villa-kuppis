"""Read-only pump parity check (card H1.3): synthetic states, no network."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from villa_kuppis import pump_parity
from villa_kuppis.ha_gateway import BindingStore

from tests.test_ha_pumps import pump_attrs

NOW = "2026-10-25T12:00:00+00:00"
UNIT = "8a7b6c5d-unit"
# As the direct client reported the owner's pumps on 25 October 2026.
CAPS = {"min_heat": 10.0, "max_heat": 31.0, "min_cool": 16.0, "max_cool": 31.0,
        "min_auto": 16.0, "max_auto": 31.0, "half_step": True, "fan_speeds": 5,
        "auto_fan": True, "modes": {"Cool": True, "Heat": True, "Dry": True,
                                    "Fan": None, "Automatic": True}}
# What the MELCloud Home integration reported for them.
HA_ATTRS = {"min_temp": 7.0, "max_temp": 35.0, "target_temp_step": None,
            "hvac_modes": ["off", "heat", "cool", "auto", "dry", "fan_only"],
            "fan_modes": ["auto", "speed_1", "speed_2", "speed_3", "speed_4", "speed_5"]}


def direct_unit(**extra):
    unit = {"id": UNIT, "name": "Olohuone", "power": True, "mode": "Cool",
            "setpoint": 24.0, "fan": "Auto", "room_temp": 25.5, "in_error": False,
            "caps": CAPS, "updated_at": NOW}
    unit.update(extra)
    return unit


def ha_side(state="cool", **attrs):
    attrs = {**HA_ATTRS, **attrs}
    states = [{"entity_id": "climate.olohuone", "state": state, "attributes": pump_attrs(**attrs),
               "last_updated": NOW, "last_reported": NOW},
              {"entity_id": "climate.other", "state": "heat", "attributes": pump_attrs(),
               "last_updated": NOW}]
    registry = [{"entity_id": "climate.olohuone", "platform": "melcloud_home",
                 "unique_id": f"{UNIT}_climate"},
                {"entity_id": "climate.other", "platform": "generic_thermostat",
                 "unique_id": "x"}]
    return pump_parity.ha_pumps(states, registry, NOW)


class ParityTests(unittest.TestCase):
    def test_matching_unit_reports_no_differences(self):
        ha = ha_side(current_temperature=25.0)
        self.assertEqual([h["entity_id"] for h in ha], ["climate.olohuone"])
        lines, pairs = pump_parity.report([direct_unit()], ha)
        self.assertEqual(len(pairs), 1)
        self.assertIn("same", lines[0])

    def test_differences_are_listed(self):
        lines, _ = pump_parity.report([direct_unit(setpoint=23.0, fan="Two")],
                                      ha_side(fan_mode="speed_5"))
        self.assertEqual(lines[1:3], ["    setpoint: MELCloud 23.0, HA 24.0",
                                      "    fan: MELCloud 'Two', HA 'Five'"])

    def test_an_off_unit_hides_its_mode_without_a_difference(self):
        lines, _ = pump_parity.report([direct_unit(power=False)], ha_side("off"))
        self.assertIn("same", lines[0])

    def test_units_match_by_name_without_an_id(self):
        ha = ha_side()
        ha[0]["unique_id"] = "something-else"
        pairs, only_direct, only_ha = pump_parity.match([direct_unit()], ha)
        self.assertEqual((len(pairs), only_direct, only_ha), (1, [], []))

    def test_compare_binds_matched_units_without_writing_to_devices(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.json"
            config.write_text(json.dumps({"data_dir": tmp, "backup_dir": str(Path(tmp) / "backup")}))
            out = []

            async def direct(_config):
                return [direct_unit()]

            async def ha(_config):
                return ha_side()
            code = pump_parity.main(["--config", str(config), "compare", "--bind"],
                                    read_direct=direct, read_ha=ha, out=out.append)
            self.assertEqual(code, 0, out)
            store = BindingStore(Path(tmp) / "sites.db")
            try:
                [binding] = store.bindings("home")
            finally:
                store.close()
            self.assertEqual((binding.device_id, binding.entity_id), (UNIT, "climate.olohuone"))

    def test_a_read_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.json"
            config.write_text(json.dumps({"data_dir": tmp, "backup_dir": str(Path(tmp) / "backup")}))
            out = []

            async def broken(_config):
                raise ConnectionError("no route")
            code = pump_parity.main(["--config", str(config), "compare"],
                                    read_direct=broken, read_ha=broken, out=out.append)
            self.assertEqual(code, 1)
            self.assertIn("no route", out[0])


if __name__ == "__main__":
    unittest.main()
