"""Service: fixture import, refresh, plan recomputation, failure isolation."""

import urllib.error
from datetime import date, datetime
from decimal import Decimal
from unittest import mock

from common import (FIXTURES, HELSINKI, NORMAL_DAY, FakeTransport,
                    TempServiceTest, elering_payload)
from villa_kuppis.prices import PriceFetchError, PriceValidationError


class TestFixtureImport(TempServiceTest):
    def test_import_saved_normal_fixture_is_offline(self):
        transport = FakeTransport()
        service = self.make_service(transport)
        result = service.import_fixture(FIXTURES / "elering-2026-10-20.json")
        assert result.day == NORMAL_DAY
        assert result.interval_count == 96
        rows = service.stored_intervals(NORMAL_DAY)
        assert rows is not None and len(rows) == 96
        assert transport.urls == [], "fixture import must not fetch over HTTP"

    def test_import_saved_dst_fixtures(self):
        service = self.make_service(FakeTransport())
        spring = service.import_fixture(FIXTURES / "elering-2026-03-29.json")
        autumn = service.import_fixture(FIXTURES / "elering-2026-10-25.json")
        assert spring.interval_count == 92
        assert autumn.interval_count == 100

    def test_import_with_explicit_matching_date(self):
        service = self.make_service(FakeTransport())
        result = service.import_fixture(FIXTURES / "elering-2026-10-20.json",
                                        day=NORMAL_DAY)
        assert result.day == NORMAL_DAY

    def test_import_under_wrong_date_rejected(self):
        service = self.make_service(FakeTransport())
        try:
            service.import_fixture(FIXTURES / "elering-2026-10-20.json",
                                   day=date(2026, 10, 21))
        except PriceValidationError:
            pass
        else:
            raise AssertionError("fixture accepted under the wrong day")
        assert service.stored_intervals(NORMAL_DAY) is None


class TestRefresh(TempServiceTest):
    def test_refresh_default_day_is_tomorrow(self):
        # Clock reads 2026-10-19 11:00 EEST -> tomorrow is 2026-10-20.
        transport = FakeTransport(elering_payload(NORMAL_DAY))
        service = self.make_service(transport)
        assert service.tomorrow() == NORMAL_DAY
        result = service.refresh()
        assert result.day == NORMAL_DAY
        assert result.interval_count == 96
        rows = service.stored_intervals(NORMAL_DAY)
        assert rows is not None and len(rows) == 96

    def test_tomorrow_is_helsinki_not_utc_date(self):
        # 2026-10-20 01:00 EEST is 2026-10-19 22:00 UTC: the Helsinki
        # calendar day is 10-20, so tomorrow must be 10-21.
        self.clock[0] = datetime(2026, 10, 20, 1, 0, 0, tzinfo=HELSINKI)
        day = date(2026, 10, 21)
        transport = FakeTransport(elering_payload(day))
        service = self.make_service(transport)
        assert service.tomorrow() == day
        assert service.refresh().day == day

    def test_http_failure_preserves_previous_day(self):
        service = self.make_service(FakeTransport(elering_payload(NORMAL_DAY)))
        service.refresh()
        before = service.stored_intervals(NORMAL_DAY)
        assert before is not None
        service._transport = FakeTransport(urllib.error.URLError("down"))
        try:
            service.refresh()
        except PriceFetchError:
            pass
        else:
            raise AssertionError("network failure was not raised")
        after = service.stored_intervals(NORMAL_DAY)
        assert after is not None
        assert [(i.ts_utc, i.price_c_kwh) for i in before] == \
               [(i.ts_utc, i.price_c_kwh) for i in after]

    def test_timeout_preserves_previous_day(self):
        service = self.make_service(FakeTransport(elering_payload(NORMAL_DAY)))
        service.refresh()
        service._transport = FakeTransport(TimeoutError("slow"))
        try:
            service.refresh()
        except PriceFetchError:
            pass
        else:
            raise AssertionError("timeout was not raised")
        assert service.stored_intervals(NORMAL_DAY) is not None

    def test_invalid_payload_preserves_previous_day(self):
        service = self.make_service(FakeTransport(elering_payload(NORMAL_DAY)))
        service.refresh()
        service._transport = FakeTransport(
            elering_payload(NORMAL_DAY, drop={4}))  # one interval missing
        try:
            service.refresh()
        except PriceValidationError:
            pass
        else:
            raise AssertionError("incomplete day was accepted")
        rows = service.stored_intervals(NORMAL_DAY)
        assert rows is not None and len(rows) == 96

    def test_plan_recomputed_after_successful_refresh(self):
        s = {"site": {"base_temperature_c": 19.0,
                      "rules": [{"kind": "heat", "threshold_c_kwh": 2.0,
                                 "target_c": 21.0}]},
             "rooms": {"room": {}}}
        # Raw 10 EUR/MWh = 1.0 c/kWh: below the 2.0 heat threshold everywhere.
        cheap = [10.0] * 96
        service = self.make_service(FakeTransport(elering_payload(NORMAL_DAY,
                                                                  cheap)))
        service.refresh()
        first = service.get_plan(NORMAL_DAY, s)
        assert all(r.reason == "threshold" for r in first["room"])
        # Same day republished at 30 EUR/MWh = 3.0 c/kWh: above the threshold.
        pricey = [30.0] * 96
        service._transport = FakeTransport(elering_payload(NORMAL_DAY, pricey))
        result = service.refresh()
        assert result.interval_count == 96
        second = service.get_plan(NORMAL_DAY, s)
        assert all(r.reason == "base" for r in second["room"])
        assert first != second


class TestGetPlan(TempServiceTest):
    def test_no_stored_day_raises_lookup(self):
        service = self.make_service(FakeTransport())
        s = {"site": {"base_temperature_c": 19.0, "rules": []},
             "rooms": {"room": {}}}
        try:
            service.get_plan(NORMAL_DAY, s)
        except LookupError:
            pass
        else:
            raise AssertionError("plan for a missing day was returned")


class TestScheduleStorageFailure(TempServiceTest):
    """sqlite3.Error mid-replacement: old day kept, error recorded."""

    def test_db_failure_keeps_old_day_and_records_error(self):
        import sqlite3

        from villa_kuppis.storage import PriceStore
        tomorrow = NORMAL_DAY
        today = date(2026, 10, 19)
        transport = FakeTransport(elering_payload(tomorrow),
                                  elering_payload(today))
        service = self.make_service(transport)
        # 15:00 EEST: the scheduler is allowed to attempt.
        self.clock[0] = datetime(2026, 10, 19, 15, 0, 0, tzinfo=HELSINKI)
        # Seed a complete day first (manual refresh, transport response #1).
        service.refresh(day=tomorrow)
        before = service.stored_intervals(tomorrow)
        assert before is not None and len(before) == 96

        def boom(self, day, intervals, retrieved_at_utc):
            raise sqlite3.OperationalError("simulated mid-insert failure")

        with mock.patch.object(PriceStore, "replace_day", boom):
            outcome = service.schedule_once()  # uses transport response #2
        assert outcome.status == "error"
        assert "simulated mid-insert failure" in outcome.detail
        after = service.stored_intervals(tomorrow)
        assert after is not None
        assert [(i.ts_utc, i.price_c_kwh) for i in before] == \
               [(i.ts_utc, i.price_c_kwh) for i in after], \
            "old complete day must survive a storage failure"
        attempt = service.store.attempt("2026-10-19")   # today's attempt
        assert attempt is not None and attempt.outcome == "error"
        assert service.schedule_once().status == "waiting", \
            "no retry inside the retry delay after a recorded failure"
