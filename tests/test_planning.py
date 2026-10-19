"""Planning: pure rule evaluation, strict thresholds, bounds, overrides."""

import copy
from decimal import Decimal

from villa_kuppis.planning import Period, normalize_settings, plan_day
import unittest

BASE = 19.0


def settings(base=BASE, rules=None, rooms=None):
    return {
        "site": {
            "base_temperature_c": base,
            "rules": [] if rules is None else rules,
        },
        "rooms": {"room": {}} if rooms is None else rooms,
    }


def heat(threshold, target):
    return {"kind": "heat", "threshold_c_kwh": threshold, "target_c": target}


def cool(threshold, target):
    return {"kind": "cool", "threshold_c_kwh": threshold, "target_c": target}


def one(settings_obj, price, ts=0, room="room"):
    """Plan a single period and return the requested room's RoomPlan."""
    plan = plan_day([Period(ts, Decimal(str(price)))], settings_obj)
    return plan[room][0]


class TestStrictBoundary(unittest.TestCase):
    def test_price_equal_to_threshold_does_not_match(self):
        row = one(settings(rules=[heat(8, 24)]), "8.0")
        assert row.temperature_c == 19
        assert row.reason == "base"
        assert row.matched_threshold_c_kwh is None

    def test_price_just_below_threshold_matches(self):
        row = one(settings(rules=[heat(8, 24)]), "7.999")
        assert row.temperature_c == 24
        assert row.reason == "threshold"
        assert row.matched_threshold_c_kwh == Decimal("8")

    def test_negative_prices_match_low_thresholds(self):
        row = one(settings(rules=[heat(-1, 20)]), "-2.5")
        assert row.temperature_c == 20
        assert row.reason == "threshold"


class TestHeating(unittest.TestCase):
    def test_highest_matching_target_wins(self):
        s = settings(rules=[heat(8, 21), heat(5, 24), heat(2, 26)])
        row = one(s, "4.9")
        assert row.temperature_c == 24  # rules 8 and 5 match: max(21, 24)
        row = one(s, "1.9")
        assert row.temperature_c == 26  # all three match

    def test_target_below_base_clamped_up_to_base(self):
        row = one(settings(rules=[heat(8, 15)]), "4.0")
        assert row.temperature_c == 19  # max(19, 15)
        assert row.reason == "threshold"  # a rule matched; the clamp landed on base


class TestCooling(unittest.TestCase):
    def test_lowest_matching_target_wins(self):
        s = settings(rules=[cool(8, 17), cool(5, 15)])
        row = one(s, "6.0")
        assert row.temperature_c == 17  # only the 8 rule matches
        row = one(s, "4.0")
        assert row.temperature_c == 15  # both match: min(17, 15)

    def test_target_above_base_clamped_down_to_base(self):
        row = one(settings(base=16.0, rules=[cool(8, 17)]), "7.0")
        assert row.temperature_c == 16  # min(16, 17)
        assert row.reason == "threshold"


class TestOverlap(unittest.TestCase):
    """A room uses one kind (heat OR cool); mixing is a config error."""

    def test_mixed_site_rules_rejected(self):
        try:
            normalize_settings(settings(rules=[cool(8, 15), heat(8, 24)]))
        except ValueError as exc:
            assert "mix" in str(exc)
        else:
            raise AssertionError("mixed site rules were accepted")

    def test_mixed_room_rules_rejected(self):
        # Site heats; a room's own rule list mixing both kinds is a
        # config error (the room list replaces, it does not extend).
        s = settings(rules=[heat(8, 24)],
                     rooms={"room": {},
                            "pump": {"rules": [heat(2, 20), cool(5, 17)]}})
        try:
            normalize_settings(s)
        except ValueError as exc:
            assert "pump" in str(exc)
        else:
            raise AssertionError("mixed room rules were accepted")

    def test_distinct_rooms_can_use_different_kinds(self):
        s = settings(rooms={"heater": {"rules": [heat(8, 24)]},
                            "pump": {"rules": [cool(8, 15)]}})
        plan = plan_day([Period(0, Decimal("7.0"))], s)
        assert plan["heater"][0].temperature_c == 24
        assert plan["heater"][0].reason == "threshold"
        assert plan["pump"][0].temperature_c == 15
        assert plan["pump"][0].reason == "threshold"

    def test_no_rule_matches_gives_base(self):
        s = settings(rooms={"heater": {"rules": [heat(8, 24)]},
                            "pump": {"rules": [cool(8, 15)]}})
        plan = plan_day([Period(0, Decimal("9.0"))], s)
        for room in ("heater", "pump"):
            assert plan[room][0].temperature_c == 19
            assert plan[room][0].reason == "base"

    def test_result_is_independent_of_rule_order(self):
        prices = ("1.0", "2.5", "4.0", "7.0", "8.0", "9.0")
        periods = [Period(1_760_000_000 + i * 900, Decimal(price))
                   for i, price in enumerate(prices)]
        rules = [heat(8, 24), heat(5, 22), heat(3, 12)]
        a = plan_day(periods, settings(rules=rules))
        b = plan_day(periods, settings(rules=list(reversed(rules))))
        assert a == b


class TestOpposingOrder(unittest.TestCase):
    """Threshold and target orders reversed: selection follows the target
    (heating: highest target; cooling: lowest target), not the threshold."""

    def test_heating_target_order_opposite_to_threshold_order(self):
        # Higher threshold, lower target: the selection must still pick
        # the highest matching target, not the highest threshold.
        s = settings(rules=[heat(8, 21), heat(5, 24)])
        row = one(s, "4.9")
        assert row.temperature_c == 24
        assert row.matched_threshold_c_kwh == Decimal("5")

    def test_cooling_target_order_opposite_to_threshold_order(self):
        # Higher threshold, lower target again, but cooling picks the
        # LOWEST matching target.
        s = settings(rules=[cool(8, 15), cool(5, 17)])
        row = one(s, "4.9")
        assert row.temperature_c == 15
        assert row.matched_threshold_c_kwh == Decimal("8")


class TestRoomOverrides(unittest.TestCase):
    def test_omitted_fields_inherit_site_values(self):
        s = settings(rules=[heat(2, 21)], rooms={"roomA": {}})
        row = one(s, "1.0", room="roomA")
        assert row.temperature_c == 21
        assert row.reason == "threshold"

    def test_per_field_base_override(self):
        s = settings(rules=[heat(2, 21)],
                     rooms={"roomB": {"base_temperature_c": 16.0}})
        row = one(s, "1.0", room="roomB")
        assert row.temperature_c == 21  # max(16, 21)
        row = one(s, "9.0", room="roomB")
        assert row.temperature_c == 16  # falls back to its own base
        assert row.reason == "base"

    def test_explicit_empty_rules_disable_inherited(self):
        s = settings(rules=[heat(2, 21)],
                     rooms={"roomC": {"rules": []}})
        row = one(s, "1.0", room="roomC")
        assert row.temperature_c == 19
        assert row.reason == "base"

    def test_room_rule_override_replaces_site_rules(self):
        s = settings(rules=[heat(8, 24)],
                     rooms={"room": {}, "roomD": {"rules": [heat(15, 16)]}})
        # Room's own rule (15 -> 16, clamped up to the base 19) replaces
        # the site rule (8 -> 24), which would give 24 at these prices.
        assert one(s, "5.0", room="roomD").temperature_c == 19
        assert one(s, "1.0", room="roomD").temperature_c == 19
        assert one(s, "5.0", room="room").temperature_c == 24  # control: site rule


class TestSettingsValidation(unittest.TestCase):
    def test_invalid_settings_rejected(self):
        bad = [
            {},
            {"site": {}},
            {"site": {"rules": []}},  # missing base_temperature_c
            {"site": {"base_temperature_c": 19, "rules": "nope"}},
            {"site": {"base_temperature_c": 19}, "rooms": "nope"},
            {"site": {"base_temperature_c": 19},
             "rooms": {"r": "nope"}},
            settings(rules=[{"kind": "heat", "threshold_c_kwh": 8}]),
            settings(rules=[{"kind": "warm", "threshold_c_kwh": 8,
                             "target_c": 20}]),
            settings(rules=[heat("eight", 20)]),
            settings(rules=[heat(True, 20)]),
            settings(rules=[heat(8, float("nan"))]),
            settings(rules=[heat(8, 24), cool(3, 15)]),  # mixed kinds
        ]
        for item in bad:
            try:
                normalize_settings(item)
            except ValueError:
                pass
            else:
                raise AssertionError(f"{item!r} was not rejected")

    def test_valid_settings_normalize(self):
        s = settings(rules=[heat(8, 24), heat(3, 15)],
                     rooms={"r": {"base_temperature_c": 18, "rules": []}})
        site_base, site_rules, rooms = normalize_settings(s)
        assert site_base == Decimal("19")
        assert len(site_rules) == 2
        assert rooms["r"] == (Decimal("18"), [])


class TestPurity(unittest.TestCase):
    def test_repeatable_and_no_settings_mutation(self):
        s = settings(rules=[heat(8, 24), heat(3, 15)],
                     rooms={"r": {"base_temperature_c": 18}})
        snapshot = copy.deepcopy(s)
        periods = [Period(1_760_000_000 + i * 900, Decimal(str(i + 1)))
                   for i in range(5)]
        first = plan_day(periods, s)
        second = plan_day(periods, s)
        assert first == second
        assert s == snapshot, "settings dict was mutated by planning"

    def test_plan_depends_only_on_price_and_settings(self):
        s = settings(rules=[heat(8, 24)])
        a = one(s, "1.0", ts=100)
        b = one(s, "1.0", ts=999_999)
        assert a.temperature_c == b.temperature_c
        assert a.reason == b.reason
