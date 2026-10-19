"""Day bounds, URL building, price conversion and validation. All offline."""

import urllib.error
import urllib.parse
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import common
from common import AUTUMN_DAY, FIXTURES, NORMAL_DAY, SPRING_DAY, elering_payload
from villa_kuppis.prices import (API_URL, INTERVAL_SECONDS, PriceFetchError,
                                 PriceInterval, PriceValidationError,
                                 build_url, expected_interval_count,
                                 fetch_day, helsinki_day_bounds,
                                 to_decimal_price, validate_day)

UTC = ZoneInfo("UTC")


def utc_ts(*args) -> int:
    return int(datetime(*args, tzinfo=UTC).timestamp())


class TestDayBounds(unittest.TestCase):
    def test_normal_day_is_24h(self):
        start, end = helsinki_day_bounds(date(2026, 10, 20))
        assert end - start == 24 * 3600
        assert expected_interval_count(date(2026, 10, 20)) == 96

    def test_spring_dst_day_is_23h(self):
        start, end = helsinki_day_bounds(SPRING_DAY)
        assert end - start == 23 * 3600
        assert expected_interval_count(SPRING_DAY) == 92

    def test_autumn_dst_day_is_25h(self):
        start, end = helsinki_day_bounds(AUTUMN_DAY)
        assert end - start == 25 * 3600
        assert expected_interval_count(AUTUMN_DAY) == 100

    def test_mindnights_convert_to_utc_independently(self):
        # 2026-10-20 is EEST (UTC+3): both midnights land on 21:00Z.
        start, end = helsinki_day_bounds(date(2026, 10, 20))
        assert start == utc_ts(2026, 10, 19, 21, 0)
        assert end == utc_ts(2026, 10, 20, 21, 0)

    def test_spring_mindnights_use_different_offsets(self):
        # 2026-03-29 00:00 EET (UTC+2) -> 2026-03-28 22:00Z
        # 2026-03-30 00:00 EEST (UTC+3) -> 2026-03-29 21:00Z
        start, end = helsinki_day_bounds(SPRING_DAY)
        assert start == utc_ts(2026, 3, 28, 22, 0)
        assert end == utc_ts(2026, 3, 29, 21, 0)

    def test_autumn_mindnights_use_different_offsets(self):
        # 2026-10-25 00:00 EEST (UTC+3) -> 2026-10-24 21:00Z
        # 2026-10-26 00:00 EET (UTC+2) -> 2026-10-25 22:00Z
        start, end = helsinki_day_bounds(AUTUMN_DAY)
        assert start == utc_ts(2026, 10, 24, 21, 0)
        assert end == utc_ts(2026, 10, 25, 22, 0)


class TestUrl(unittest.TestCase):
    """start/end must be UTC date-time strings (official OpenAPI spec)."""

    def _params(self, day: date) -> dict:
        parts = urllib.parse.urlsplit(build_url(day))
        assert parts.scheme == "https"
        assert parts.netloc == "dashboard.elering.ee"
        assert parts.path == "/api/nps/price"
        return urllib.parse.parse_qs(parts.query)

    def test_normal_day_url_is_literal_utc_date_times(self):
        # 2026-10-20 Helsinki = [2026-10-19T21:00:00Z, 2026-10-20T21:00:00Z)
        # (EEST, UTC+3); the API end is the second before next midnight.
        assert self._params(NORMAL_DAY) == {
            "start": ["2026-10-19T21:00:00Z"],
            "end": ["2026-10-20T20:59:59Z"],
        }

    def test_spring_dst_day_url_is_literal_utc_date_times(self):
        # 2026-03-29 Helsinki: local midnight 00:00 EET -> 22:00Z, next
        # midnight 00:00 EEST -> 21:00Z (23 h day).
        assert self._params(SPRING_DAY) == {
            "start": ["2026-03-28T22:00:00Z"],
            "end": ["2026-03-29T20:59:59Z"],
        }

    def test_autumn_dst_day_url_is_literal_utc_date_times(self):
        # 2026-10-25 Helsinki: local midnight 00:00 EEST -> 21:00Z, next
        # midnight 00:00 EET -> 22:00Z (25 h day).
        assert self._params(AUTUMN_DAY) == {
            "start": ["2026-10-24T21:00:00Z"],
            "end": ["2026-10-25T21:59:59Z"],
        }

    def test_url_values_are_real_utc_date_times(self):
        for day in (NORMAL_DAY, SPRING_DAY, AUTUMN_DAY):
            params = self._params(day)
            for name in ("start", "end"):
                value = datetime.fromisoformat(
                    params[name][0].replace("Z", "+00:00"))
                assert value.utcoffset() == timedelta(0)


class TestConversion(unittest.TestCase):
    def test_raw_over_10_is_c_per_kwh(self):
        assert PriceInterval(0, Decimal("12.34")).price_c_kwh == Decimal("1.234")

    def test_negative_price_is_valid(self):
        assert PriceInterval(0, Decimal("-4.0")).price_c_kwh == Decimal("-0.4")

    def test_accepts_int_and_float(self):
        assert to_decimal_price(3) == Decimal(3)
        assert to_decimal_price(-2.5) == Decimal("-2.5")

    def test_rejects_malformed(self):
        for bad in (None, "1.5", True, False, float("nan"), float("inf")):
            try:
                to_decimal_price(bad)
            except PriceValidationError:
                pass
            else:
                raise AssertionError(f"{bad!r} was not rejected")


class TestValidateDay(unittest.TestCase):
    def test_saved_normal_fixture_is_valid(self):
        payload = (FIXTURES / "elering-2026-10-20.json").read_bytes()
        intervals = validate_day(payload, NORMAL_DAY)
        assert len(intervals) == 96
        assert [i.ts_utc for i in intervals] == sorted(i.ts_utc for i in intervals)
        assert any(i.raw_price_eur_mwh < 0 for i in intervals)

    def test_saved_dst_fixtures_are_valid(self):
        spring = (FIXTURES / "elering-2026-03-29.json").read_bytes()
        autumn = (FIXTURES / "elering-2026-10-25.json").read_bytes()
        assert len(validate_day(spring, SPRING_DAY)) == 92
        assert len(validate_day(autumn, AUTUMN_DAY)) == 100

    def test_full_synthetic_day_passes(self):
        intervals = validate_day(elering_payload(NORMAL_DAY), NORMAL_DAY)
        assert len(intervals) == 96
        assert intervals[0].raw_price_eur_mwh == Decimal(1)

    def test_success_false_rejected(self):
        for omit in ("success", "data", "fi", "fi_empty"):
            try:
                validate_day(elering_payload(NORMAL_DAY, omit=omit), NORMAL_DAY)
            except PriceValidationError:
                pass
            else:
                raise AssertionError(f"omit={omit} was not rejected")

    def test_incomplete_day_rejected_without_zero_fill(self):
        payload = elering_payload(NORMAL_DAY, drop={5})
        try:
            validate_day(payload, NORMAL_DAY)
        except PriceValidationError as exc:
            assert "missing interval" in str(exc)
        else:
            raise AssertionError("incomplete day was accepted")

    def test_duplicate_timestamp_rejected(self):
        start, _ = helsinki_day_bounds(NORMAL_DAY)
        payload = elering_payload(NORMAL_DAY,
                                  extra=[(start + 3 * INTERVAL_SECONDS, 1.0)])
        try:
            validate_day(payload, NORMAL_DAY)
        except PriceValidationError as exc:
            assert "duplicate" in str(exc)
        else:
            raise AssertionError("duplicate timestamp was accepted")

    def test_outside_bounds_rejected(self):
        start, end = helsinki_day_bounds(NORMAL_DAY)
        for ts in (start - INTERVAL_SECONDS, end):
            payload = elering_payload(NORMAL_DAY, extra=[(ts, 1.0)])
            try:
                validate_day(payload, NORMAL_DAY)
            except PriceValidationError as exc:
                assert "outside the requested day" in str(exc)
            else:
                raise AssertionError(f"ts {ts} was accepted")

    def test_off_grid_timestamp_rejected(self):
        start, _ = helsinki_day_bounds(NORMAL_DAY)
        payload = elering_payload(NORMAL_DAY, drop={7},
                                  extra=[(start + 7 * INTERVAL_SECONDS + 450, 1.0)])
        try:
            validate_day(payload, NORMAL_DAY)
        except PriceValidationError as exc:
            assert "not on the 900 s grid" in str(exc)
        else:
            raise AssertionError("off-grid timestamp was accepted")

    def test_bad_json_rejected(self):
        try:
            validate_day(b"not json at all", NORMAL_DAY)
        except PriceValidationError as exc:
            assert "JSON" in str(exc)
        else:
            raise AssertionError("bad JSON was accepted")

    def test_malformed_prices_rejected(self):
        prices: list = [1.0] * 96
        for i, bad in ((10, "1.5"), (20, None), (30, float("nan")),
                       (40, float("inf"))):
            prices[i] = bad
        try:
            validate_day(elering_payload(NORMAL_DAY, prices), NORMAL_DAY)
        except PriceValidationError:
            pass
        else:
            raise AssertionError("malformed price was accepted")


class TestFetch(unittest.TestCase):
    def test_fetch_uses_injected_transport_and_returns_intervals(self):
        from common import FakeTransport
        transport = FakeTransport(elering_payload(NORMAL_DAY))
        intervals = fetch_day(NORMAL_DAY, transport=transport)
        assert len(intervals) == 96
        assert transport.urls == [build_url(NORMAL_DAY)]

    def test_network_error_raises_fetch_error(self):
        from common import FakeTransport
        transport = FakeTransport(urllib.error.URLError("boom"))
        try:
            fetch_day(NORMAL_DAY, transport=transport)
        except PriceFetchError:
            pass
        else:
            raise AssertionError("network error was not raised")

    def test_timeout_raises_fetch_error(self):
        from common import FakeTransport
        transport = FakeTransport(TimeoutError("timed out"))
        try:
            fetch_day(NORMAL_DAY, transport=transport)
        except PriceFetchError:
            pass
        else:
            raise AssertionError("timeout was not raised")
