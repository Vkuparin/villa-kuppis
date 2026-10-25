"""Cottage bindings follow what the cottage integration publishes.

Each room status sensor lists the room's devices (``devices`` attribute);
Villa Kuppis binds them under stable device ids, so moving from the
stand-in devices to the real ones is only an edit of the cottage package.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from villa_kuppis import cottage
from villa_kuppis.cottage import CottageSite
from villa_kuppis.ha_gateway import BindingStore


class StubGateway:
    def __init__(self):
        self.attrs = {}
        self.reloads = 0

    def attributes(self, entity_id):
        attrs = self.attrs.get(entity_id)
        return dict(attrs) if attrs is not None else None

    def snapshot(self):
        return {}

    def reload_bindings(self):
        self.reloads += 1

    def start(self):
        pass

    def stop(self):
        pass


def publish(gateway, room, devices):
    gateway.attrs[cottage.status_entity(room)] = {"mode": "fallback", "devices": devices}


SIMULATED = {
    "temperature": "sensor.sim_alpstuga_large_temperature",
    "humidity": "sensor.sim_alpstuga_large_humidity",
    "co2": "sensor.sim_alpstuga_large_carbon_dioxide",
    "pm25": "sensor.sim_alpstuga_large_pm25",
    "heaters": ["climate.sim_mill_large_1", "climate.sim_mill_large_2",
                "climate.sim_mill_large_3"],
}


class FollowPublishedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = BindingStore(Path(self.tmp.name) / cottage.SITES_DB)
        self.gateway = StubGateway()
        self.site = CottageSite(self.tmp.name, self.gateway,
                                lambda: self.store.bindings("cottage"),
                                binding_store=self.store)

    def tearDown(self):
        self.site._store.close()
        self.site._visits.close()
        self.store.close()
        self.tmp.cleanup()

    def bound(self):
        return {b.device_id: b.entity_id for b in self.store.bindings("cottage")}

    def test_published_devices_are_bound_with_labels(self):
        publish(self.gateway, "large", SIMULATED)
        self.gateway.attrs["climate.sim_mill_large_3"] = {"friendly_name": "Sim Mill 500 W"}
        self.assertTrue(self.site.follow_published())
        self.assertEqual(self.bound(), {
            "large-status": "sensor.villa_kuppis_large_target",
            "large-temperature": SIMULATED["temperature"],
            "large-humidity": SIMULATED["humidity"],
            "large-co2": SIMULATED["co2"],
            "large-pm25": SIMULATED["pm25"],
            "large-heater-1": "climate.sim_mill_large_1",
            "large-heater-2": "climate.sim_mill_large_2",
            "large-heater-3": "climate.sim_mill_large_3",
        })
        labels = {b.device_id: b.label for b in self.store.bindings("cottage")}
        self.assertEqual(labels["large-heater-3"], "Sim Mill 500 W")
        self.assertTrue(all(b.room == "large" for b in self.store.bindings("cottage")))
        self.assertFalse(self.site.follow_published())   # nothing new

    def test_switching_to_real_devices_keeps_the_device_ids(self):
        publish(self.gateway, "large", SIMULATED)
        self.site.follow_published()
        real = {"temperature": "sensor.alpstuga_iso_huone_temperature",
                "heaters": ["climate.mill_iso_huone_2", "climate.mill_iso_huone_1"]}
        publish(self.gateway, "large", real)
        self.assertTrue(self.site.follow_published())
        self.assertEqual(self.bound(), {
            "large-status": "sensor.villa_kuppis_large_target",
            "large-temperature": "sensor.alpstuga_iso_huone_temperature",
            "large-heater-1": "climate.mill_iso_huone_2",
            "large-heater-2": "climate.mill_iso_huone_1",
        })
        # The two heaters swap device ids without a unique-entity conflict.
        publish(self.gateway, "large", {**real, "heaters": list(reversed(real["heaters"]))})
        self.site.follow_published()
        self.assertEqual(self.bound()["large-heater-1"], "climate.mill_iso_huone_1")

    def test_rooms_without_published_devices_are_left_alone(self):
        self.store.bind("cottage", "small-temperature", "sensor.manual_small", "sensor",
                        "ALPSTUGA", "small")
        self.gateway.attrs[cottage.status_entity("small")] = {"mode": "fallback"}
        self.assertFalse(self.site.follow_published())   # older integration
        self.assertEqual(self.bound(), {"small-temperature": "sensor.manual_small"})

    def test_bad_entries_are_ignored(self):
        publish(self.gateway, "small", {"temperature": "climate.oops", "heaters": "x"})
        self.site.follow_published()
        self.assertEqual(self.bound(), {"small-status": "sensor.villa_kuppis_small_target"})

    def test_sampling_follows_and_tells_the_gateway(self):
        publish(self.gateway, "small", {"temperature": "sensor.sim_alpstuga_small_temperature",
                                        "heaters": ["climate.sim_mill_small_1"]})
        self.site.sample()
        self.assertEqual(len(self.bound()), 3)
        self.assertEqual(self.gateway.reloads, 1)


if __name__ == "__main__":
    unittest.main()
