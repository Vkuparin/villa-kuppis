"""SQLite storage: atomic day replacement, re-import, rollback, attempts."""

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from common import AUTUMN_DAY, NORMAL_DAY, SPRING_DAY
from villa_kuppis.prices import INTERVAL_SECONDS, PriceInterval
from villa_kuppis.storage import PriceStore

NOW = datetime(2026, 10, 19, 10, 0, 0, tzinfo=timezone.utc)


def make_intervals(day, price: Decimal = Decimal("1.00")):
    from villa_kuppis.prices import helsinki_day_bounds
    start, end = helsinki_day_bounds(day)
    return [PriceInterval(start + i * INTERVAL_SECONDS, price)
            for i in range((end - start) // INTERVAL_SECONDS)]


def stored(store: PriceStore, day) -> list:
    """stored_day, asserted non-None (the day was just written)."""
    rows = store.stored_day(day.isoformat())
    assert rows is not None
    return rows


def stored_attempt(store: PriceStore, day: str) -> str:
    attempt = store.attempt(day)
    assert attempt is not None
    return attempt.outcome


class TestPriceStorage(unittest.TestCase):
    def _store(self, tmp: Path) -> PriceStore:
        return PriceStore(tmp / "prices.db")

    def test_store_and_read_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(Path(tmp))
            intervals = make_intervals(NORMAL_DAY, Decimal("3.14"))
            store.replace_day(NORMAL_DAY.isoformat(), intervals, NOW)
            rows = stored(store, NORMAL_DAY)
            assert len(rows) == 96
            assert rows[0].price_c_kwh == Decimal("3.14") / Decimal(10)
            assert rows[0].retrieved_at_utc == NOW.isoformat()
            store.close()

    def test_missing_day_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(Path(tmp))
            assert store.stored_day("2026-01-01") is None
            store.close()

    def test_reimport_same_day_no_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(Path(tmp))
            store.replace_day(NORMAL_DAY.isoformat(),
                              make_intervals(NORMAL_DAY, Decimal("1.00")), NOW)
            store.replace_day(NORMAL_DAY.isoformat(),
                              make_intervals(NORMAL_DAY, Decimal("2.00")), NOW)
            rows = stored(store, NORMAL_DAY)
            assert len(rows) == 96
            assert all(s.price_c_kwh == Decimal("2.00") / Decimal(10)
                       for s in rows)
            store.close()

    def test_changed_replacement_no_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(Path(tmp))
            first = make_intervals(NORMAL_DAY)
            changed = [PriceInterval(iv.ts_utc + 0,
                                     Decimal("9.99")) for iv in first]
            store.replace_day(NORMAL_DAY.isoformat(), first, NOW)
            store.replace_day(NORMAL_DAY.isoformat(), changed, NOW)
            rows = stored(store, NORMAL_DAY)
            assert len(rows) == 96
            assert all(s.price_c_kwh == Decimal("9.99") / Decimal(10)
                       for s in rows)
            store.close()

    def test_dst_days_coexist_without_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(Path(tmp))
            for day in (NORMAL_DAY, SPRING_DAY, AUTUMN_DAY):
                store.replace_day(day.isoformat(), make_intervals(day), NOW)
            counts = {day.isoformat(): len(stored(store, day))
                      for day in (NORMAL_DAY, SPRING_DAY, AUTUMN_DAY)}
            assert counts == {"2026-10-20": 96, "2026-03-29": 92,
                              "2026-10-25": 100}
            total = sum(counts.values())
            assert total == 288
            store.close()

    def test_transaction_rollback_on_constraint_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(Path(tmp))
            good = make_intervals(NORMAL_DAY, Decimal("1.00"))
            store.replace_day(NORMAL_DAY.isoformat(), good, NOW)
            before = store.stored_day(NORMAL_DAY.isoformat())
            # A replacement with a duplicate ts violates the primary key
            # mid-transaction; the whole replacement must roll back.
            bad = good + [good[0]]
            try:
                store.replace_day(NORMAL_DAY.isoformat(), bad, NOW)
            except sqlite3.IntegrityError:
                pass
            else:
                raise AssertionError("duplicate ts was not rejected")
            after = stored(store, NORMAL_DAY)
            assert [(s.ts_utc, s.price_c_kwh) for s in before] == \
                   [(s.ts_utc, s.price_c_kwh) for s in after]
            store.close()


class TestAttemptState(unittest.TestCase):
    def _store(self, tmp: Path) -> PriceStore:
        return PriceStore(tmp / "prices.db")

    def test_lifecycle_running_to_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(Path(tmp))
            assert store.attempt("2026-10-19") is None
            store.mark_attempt("2026-10-19", NOW)
            assert stored_attempt(store, "2026-10-19") == "running"
            store.finish_attempt("2026-10-19", "ok")
            assert stored_attempt(store, "2026-10-19") == "ok"
            store.close()

    def test_interrupted_attempt_stays_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(Path(tmp))
            store.mark_attempt("2026-10-19", NOW)
            store.close()
            # "Restart": a fresh store on the same file.
            store2 = self._store(Path(tmp))
            assert stored_attempt(store2, "2026-10-19") == "running"
            store2.close()

    def test_finish_rejects_unknown_outcome(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(Path(tmp))
            store.mark_attempt("2026-10-19", NOW)
            try:
                store.finish_attempt("2026-10-19", "weird")
            except ValueError:
                pass
            else:
                raise AssertionError("unknown outcome was accepted")
            store.close()
