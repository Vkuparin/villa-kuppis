"""Tests for the daily SQLite backup (card F3.2).

Covers backup_once (layout, Helsinki dating, the .complete marker,
optional prices.db, failure cleanup, stop-event cancellation and the
copy wall-clock budget) and BackupLoop (immediate first run, failure
retry, the Helsinki-date rollover with a fake clock advanced inside the
wait and with a real clock and real wait, close() waking the default
stop-event wait immediately, and a restart skipping a finished day).
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from villa_kuppis.backup import (COMPLETE_MARKER, HELSENKI_TZ, BackupLoop,
                                 BackupResult, backup_once, helsinki_day)

START = datetime(2026, 10, 19, 20, 30, 0, tzinfo=timezone.utc)
# Helsinki time of START is 23:30; midnight is 30 minutes away (21:00 UTC).


class FakeClock:
    def __init__(self, now: datetime = START):
        self._now = now

    def __call__(self):
        return self._now

    def advance(self, seconds: float):
        self._now = self._now + timedelta(seconds=seconds)

    @property
    def now(self) -> datetime:
        return self._now


def wait_for(predicate, seconds=10.0, step=0.01):
    """Poll predicate until true; raise AssertionError on the deadline."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(step)
    raise AssertionError("the condition was not met within %.0fs" % seconds)


def make_db(path: Path, rows: int = 200) -> None:
    """Create a real SQLite database with a small table."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("create table t (id integer primary key, v text)")
        conn.executemany("insert into t (v) values (?)",
                         [(f"row-{i}",) for i in range(rows)])
        conn.commit()
    finally:
        conn.close()


def row_count(path: Path) -> int:
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute("select count(*) from t").fetchone()[0]
    finally:
        conn.close()


class TempDirTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="villa-kuppis-bk-")
        self.root = Path(self._tmp.name)
        self.data_dir = self.root / "data"
        self.backup_dir = self.root / "backups"
        self.data_dir.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def day_files(self, day: str) -> list:
        return sorted(p.name for p in (self.backup_dir / day).iterdir()) \
            if (self.backup_dir / day).is_dir() else []


class TestBackupOnce(TempDirTest):
    def test_writes_both_databases_with_marker(self):
        make_db(self.data_dir / "pumps.db", 200)
        make_db(self.data_dir / "prices.db", 10)
        result = backup_once(self.data_dir, self.backup_dir,
                             now=FakeClock())
        self.assertTrue(result.backed_up)
        self.assertFalse(result.skipped)
        self.assertTrue(result.ok)
        self.assertIsNone(result.error)
        self.assertEqual(result.day, "2026-10-19")  # 23:30 Helsinki
        self.assertEqual(result.files, ("pumps.db", "prices.db"))
        day = self.backup_dir / "2026-10-19"
        self.assertEqual(row_count(day / "pumps.db"), 200)
        self.assertEqual(row_count(day / "prices.db"), 10)
        self.assertTrue((day / COMPLETE_MARKER).is_file())
        # no stray partials or journal files in the day directory
        self.assertEqual(self.day_files("2026-10-19"),
                         [COMPLETE_MARKER, "prices.db", "pumps.db"])

    def test_missing_prices_db_is_optional(self):
        make_db(self.data_dir / "pumps.db", 50)
        result = backup_once(self.data_dir, self.backup_dir,
                             now=FakeClock())
        self.assertTrue(result.ok)
        self.assertEqual(result.files, ("pumps.db",))
        self.assertEqual(self.day_files("2026-10-19"),
                         [COMPLETE_MARKER, "pumps.db"])

    def test_missing_pumps_db_is_a_reported_error(self):
        result = backup_once(self.data_dir, self.backup_dir,
                             now=FakeClock())
        self.assertFalse(result.ok)
        self.assertFalse(result.backed_up)
        self.assertFalse(result.skipped)
        self.assertIsNotNone(result.error)
        self.assertIn("pumps.db", result.error)
        self.assertFalse(self.backup_dir.exists()
                         and any(self.backup_dir.iterdir()))

    def test_helsinki_date_of_the_backup_moment(self):
        make_db(self.data_dir / "pumps.db")
        # 20:30 UTC is 23:30 Helsinki (same day); 21:00 UTC is already
        # 00:00 Helsinki the next day.
        clock = FakeClock(datetime(2026, 10, 19, 20, 30, tzinfo=timezone.utc))
        self.assertEqual(backup_once(self.data_dir, self.backup_dir,
                                     now=clock).day, "2026-10-19")
        clock = FakeClock(datetime(2026, 10, 19, 21, 0, tzinfo=timezone.utc))
        self.assertEqual(backup_once(self.data_dir, self.backup_dir,
                                     now=clock).day, "2026-10-20")
        self.assertEqual(row_count(self.backup_dir / "2026-10-20"
                                   / "pumps.db"), 200)

    def test_second_call_same_day_skips(self):
        make_db(self.data_dir / "pumps.db", 200)
        self.assertTrue(backup_once(self.data_dir, self.backup_dir,
                                    now=FakeClock()).ok)
        # new rows land in the source after the finished backup
        conn = sqlite3.connect(str(self.data_dir / "pumps.db"))
        conn.execute("insert into t (v) values ('late')")
        conn.commit()
        conn.close()
        result = backup_once(self.data_dir, self.backup_dir,
                             now=FakeClock(
                                 datetime(2026, 10, 19, 20, 45,
                                          tzinfo=timezone.utc)))
        self.assertTrue(result.skipped)
        self.assertFalse(result.backed_up)
        self.assertIsNone(result.error)
        # the finished snapshot is untouched
        self.assertEqual(row_count(self.backup_dir / "2026-10-19"
                                   / "pumps.db"), 200)

    def test_corrupt_source_fails_cleanly(self):
        (self.data_dir / "pumps.db").write_bytes(b"not a database")
        result = backup_once(self.data_dir, self.backup_dir,
                             now=FakeClock())
        self.assertFalse(result.ok)
        self.assertIsNotNone(result.error)
        self.assertIn("the backup failed", result.error)
        day = self.backup_dir / "2026-10-19"
        self.assertFalse((day / COMPLETE_MARKER).exists())
        self.assertFalse(any(p.name.endswith(".tmp") for p in day.iterdir()))
        # the source is never touched or created
        self.assertEqual(self.data_dir.joinpath("pumps.db").read_bytes(),
                         b"not a database")

    def test_stop_event_already_set_cancels_before_copying(self):
        make_db(self.data_dir / "pumps.db", 200)
        stop = threading.Event()
        stop.set()
        result = backup_once(self.data_dir, self.backup_dir,
                             now=FakeClock(), stop_event=stop)
        self.assertFalse(result.ok)
        self.assertIsNotNone(result.error)
        self.assertIn("cancelled", result.error)
        day = self.backup_dir / "2026-10-19"
        self.assertFalse((day / COMPLETE_MARKER).exists())
        self.assertFalse(any(p.name.endswith(".tmp") for p in day.iterdir()))

    def test_copy_timeout_aborts_real_copy(self):
        make_db(self.data_dir / "pumps.db", 50_000)
        with patch("villa_kuppis.backup.time.monotonic", side_effect=[0.0, 1.0]):
            result = backup_once(self.data_dir, self.backup_dir,
                                 now=FakeClock(), copy_timeout=0.5)
        self.assertFalse(result.ok)
        self.assertIsNotNone(result.error)
        self.assertIn("budget", result.error)
        day = self.backup_dir / "2026-10-19"
        self.assertFalse((day / COMPLETE_MARKER).exists())
        self.assertFalse(any(p.name.endswith(".tmp") for p in day.iterdir()))

    def test_snapshot_reads_committed_wal_with_uri_sensitive_path(self):
        self.data_dir = self.root / "m\u00f6kin data #1%"
        self.data_dir.mkdir()
        source = self.data_dir / "pumps.db"
        make_db(source, 300)
        writer = sqlite3.connect(source)
        try:
            writer.execute("pragma journal_mode=wal")
            writer.execute("insert into t (v) values ('committed')")
            writer.commit()
            writer.execute("insert into t (v) values ('uncommitted')")
            result = backup_once(self.data_dir, self.backup_dir, now=FakeClock())
            self.assertTrue(result.ok, result.error)
            snap = self.backup_dir / "2026-10-19" / "pumps.db"
            self.assertEqual(row_count(snap), 301)
            with closing(sqlite3.connect(snap)) as copied:
                self.assertEqual(copied.execute("pragma integrity_check").fetchone()[0], "ok")
            writer.rollback()
            self.assertEqual(row_count(source), 301)
        finally:
            writer.close()


class TestBackupLoopFake(TempDirTest):
    """Loop mechanics with an injectable backup_fn and fake clock."""

    def test_first_run_immediate_then_one_per_helsinki_day(self):
        clock = FakeClock()
        calls = []

        def fake(data_dir, backup_dir, *, now=None, stop_event=None):
            calls.append(helsinki_day(clock.now).isoformat())
            return BackupResult(helsinki_day(clock.now).isoformat(),
                                True, False, ("pumps.db",), None)

        waits = []

        def wait(seconds):
            waits.append(seconds)
            # the clock is frozen: the date never changes, so the loop
            # must stay put for the whole day

        loop = BackupLoop(self.data_dir, self.backup_dir, now=clock,
                          wait=wait, retry_interval=5.0, backup_fn=fake)
        loop.start()
        try:
            wait_for(lambda: len(calls) == 1)
            self.assertEqual(calls, ["2026-10-19"])
            time.sleep(0.2)  # plenty of chances for a bogus second run
            self.assertEqual(calls, ["2026-10-19"])
            self.assertTrue(all(w <= 60.0 for w in waits), waits)
        finally:
            loop.close()
        self.assertEqual(len(calls), 1)  # nothing runs after close()

    def test_failed_attempts_retry_until_success(self):
        clock = FakeClock()
        calls = []

        def fake(data_dir, backup_dir, *, now=None, stop_event=None):
            calls.append(helsinki_day(clock.now).isoformat())
            if len(calls) < 3:
                return BackupResult(helsinki_day(clock.now).isoformat(),
                                    False, False, (), "simulated failure")
            return BackupResult(helsinki_day(clock.now).isoformat(),
                                True, False, ("pumps.db",), None)

        def wait(seconds):
            if len(calls) < 3:
                clock.advance(seconds)
            time.sleep(0.001)

        loop = BackupLoop(self.data_dir, self.backup_dir, now=clock,
                          wait=wait, retry_interval=300.0, backup_fn=fake)
        loop.start()
        try:
            wait_for(lambda: len(calls) >= 3)
            last = loop.last_result
            if last is None:
                self.fail("the loop never recorded a result")
            self.assertIsNone(last.error)
            self.assertTrue(last.ok)
        finally:
            loop.close()
        self.assertEqual(len(calls), 3)  # no further runs after close()
        # two 300 s retry sleeps, fully consumed before attempt 3
        self.assertEqual(clock.now - START, timedelta(seconds=600))

    def test_rollover_triggers_next_day_backup(self):
        # START is 23:30 Helsinki; the wait below advances the fake
        # clock, so the Helsinki date flips 30 fake-minutes later. The
        # loop must notice the date change and run the second backup.
        clock = FakeClock()
        calls = []

        def fake(data_dir, backup_dir, *, now=None, stop_event=None):
            calls.append(helsinki_day(clock.now).isoformat())
            return BackupResult(helsinki_day(clock.now).isoformat(), True, False,
                                ("pumps.db",), None)

        def wait(seconds):
            if len(calls) < 2:
                clock.advance(seconds)
            time.sleep(0.001)

        loop = BackupLoop(self.data_dir, self.backup_dir, now=clock,
                          wait=wait, retry_interval=300.0, backup_fn=fake)
        loop.start()
        try:
            wait_for(lambda: len(calls) >= 2)
        finally:
            loop.close()
        self.assertEqual(len(calls), 2)  # nothing runs after close()
        self.assertEqual(calls, ["2026-10-19", "2026-10-20"])

    def test_wait_is_chunked_and_close_wakes_injected_wait(self):
        clock = FakeClock()
        waits = []
        calls = []

        def fake(data_dir, backup_dir, *, now=None, stop_event=None):
            calls.append(1)
            return BackupResult("2026-10-19", False, False, (),
                                "always failing")

        def wait(seconds):
            waits.append(seconds)
            clock.advance(seconds)

        loop = BackupLoop(self.data_dir, self.backup_dir, now=clock,
                          wait=wait, retry_interval=150.0, backup_fn=fake)
        loop.start()
        try:
            wait_for(lambda: waits and sum(waits) >= 150.0)
            self.assertTrue(all(w <= 60.0 for w in waits), waits)
        finally:
            started = time.monotonic()
            loop.close()
        self.assertLess(time.monotonic() - started, 5.0)


class TestBackupLoopReal(TempDirTest):
    """Loop mechanics against the real backup and real waiting."""

    def test_real_default_close_is_fast_and_joins(self):
        make_db(self.data_dir / "pumps.db", 200)
        calls = []

        def fake(data_dir, backup_dir, *, now=None,
                 stop_event: "threading.Event | None" = None):
            calls.append(1)
            # a backup in flight honours the stop event: the real
            # backup_once does this via the progress callback
            if stop_event is None:
                self.fail("the loop did not pass its stop event")
            stop_event.wait(60)
            return BackupResult("2026-10-19", True, False,
                                ("pumps.db",), None)

        loop = BackupLoop(self.data_dir, self.backup_dir, backup_fn=fake)
        loop.start()
        try:
            wait_for(lambda: len(calls) == 1)
        except AssertionError:
            self.fail("the first run never happened")
        started = time.monotonic()
        loop.close()
        elapsed = time.monotonic() - started
        # close() must wake the blocked thread immediately, not wait
        # for the 60 s backup or any join timeout
        self.assertLess(elapsed, 5.0, elapsed)
        self.assertEqual(len(calls), 1)  # the thread is joined, done

    def test_real_cancellation_during_busy_retries(self):
        # A writer holding an exclusive lock makes the backup API hit
        # SQLITE_BUSY on every step; CPython retries forever and calls
        # the progress callback on each retry, which is where the stop
        # event cancels the copy.
        make_db(self.data_dir / "pumps.db", 5_000)
        stop = threading.Event()
        holder = sqlite3.connect(str(self.data_dir / "pumps.db"))
        holder.isolation_level = None
        holder.execute("begin exclusive")
        try:
            result = {}

            def run():
                result["r"] = backup_once(self.data_dir, self.backup_dir,
                                          now=FakeClock(), stop_event=stop,
                                          copy_timeout=60)

            thread = threading.Thread(target=run)
            thread.start()
            time.sleep(0.3)  # let the copy enter the busy/locked retries
            stop.set()
            thread.join(15)
            self.assertFalse(thread.is_alive())
            self.assertFalse(result["r"].ok)
            self.assertIn("cancelled", result["r"].error)
            day = self.backup_dir / "2026-10-19"
            self.assertFalse((day / COMPLETE_MARKER).exists())
            self.assertFalse(any(p.name.endswith(".tmp")
                                 for p in day.iterdir()))
        finally:
            holder.execute("rollback")
            holder.close()
            stop.set()
            thread.join(5)

    def test_real_rollover_with_real_clock_and_wait(self):
        # Start the clock 1 second before the real Helsinki midnight;
        # the loop's first run happens now, then the real stop-event
        # wait sleeps until the date flips, which must trigger the
        # second backup.
        helsinki_now = datetime.now(timezone.utc).astimezone(HELSENKI_TZ)
        next_midnight = (helsinki_now.replace(hour=0, minute=0, second=0,
                                              microsecond=0)
                         + timedelta(days=1))
        base = next_midnight - timedelta(seconds=1.0)
        t0 = time.monotonic()
        clock = lambda: base + timedelta(seconds=time.monotonic() - t0)

        calls = []

        def fake(data_dir, backup_dir, *, now=None, stop_event=None):
            calls.append(helsinki_day(clock()))
            return BackupResult(helsinki_day(clock()).isoformat(), True,
                                False, ("pumps.db",), None)

        loop = BackupLoop(self.data_dir, self.backup_dir, now=clock,
                          backup_fn=fake)
        loop.start()
        try:
            wait_for(lambda: len(calls) == 2, seconds=30.0)
            self.assertEqual(calls[0], helsinki_day(base))
            self.assertEqual(calls[1], helsinki_day(next_midnight))
            self.assertNotEqual(calls[0], calls[1])
        finally:
            loop.close()


class TestRestartSkip(TempDirTest):
    def test_restart_skips_finished_day_and_retries_broken_one(self):
        make_db(self.data_dir / "pumps.db", 100)
        # a run that fails: no marker is written
        (self.data_dir / "pumps.db").unlink()
        first = backup_once(self.data_dir, self.backup_dir,
                            now=FakeClock())
        self.assertFalse(first.ok)
        self.assertFalse((self.backup_dir / "2026-10-19"
                          / COMPLETE_MARKER).exists())
        # the restart (same day) retries and this time succeeds
        make_db(self.data_dir / "pumps.db", 100)
        second = backup_once(self.data_dir, self.backup_dir,
                             now=FakeClock())
        self.assertTrue(second.backed_up)
        self.assertEqual(second.day, "2026-10-19")
        # a later restart of the same day skips the finished backup
        third = backup_once(self.data_dir, self.backup_dir,
                            now=FakeClock(
                                datetime(2026, 10, 19, 20, 45,
                                         tzinfo=timezone.utc)))
        self.assertTrue(third.skipped)
        self.assertEqual(row_count(self.backup_dir / "2026-10-19"
                                   / "pumps.db"), 100)


if __name__ == "__main__":
    unittest.main()
