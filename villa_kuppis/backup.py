"""Daily SQLite backups for the pump service data (card F3.2).

``backup_once`` snapshots ``pumps.db`` (required) and ``prices.db``
(optional) with ``sqlite3.Connection.backup``: a separate read-only
source connection opened through a URI (``Path.as_uri``, so paths with
spaces or non-ASCII names work), a consistent snapshot into a temp file
in the destination day directory, then an atomic replace onto the final
file. The backup is a consistent point-in-time copy of the committed
data; it never copies the database file or its WAL bytes directly and
never creates a missing source.

The copy itself is bounded: the backup API's progress callback is
checked on every step (including SQLite's busy/locked retries, which
CPython would otherwise repeat forever) against an optional stop event
and a monotonic wall-clock deadline (default 30 seconds per database).
Passing an already-set stop event therefore fails the attempt quickly
with a cancel error instead of copying.

Layout: ``<backup_dir>/<YYYY-MM-DD>/<pumps.db|prices.db>`` where the
date is the Helsinki date of the backup moment, plus a ``.complete``
marker written only after every existing source has succeeded. A day
whose marker already exists is skipped, which is what makes a service
restart skip the current day instead of overwriting the finished
backup. Failures leave the temp partials cleaned up, keep whatever
earlier backups (other days, and already-finished files of this day)
exist, and write no marker, so the next attempt retries the whole day.
There is no retention: old days are never deleted.

``BackupLoop`` runs the backup on a daemon thread: an immediate first
run, then one run per Helsinki date. Failures are retried every
``retry_interval`` seconds (default 300) until they succeed or the
Helsinki date changes. ``now``, ``wait`` and ``retry_interval`` are
injectable so the loop can be tested with a fake clock; the default
wait is the stop event's ``wait()``, so ``close()`` wakes the thread
immediately. ``close()`` sets the stop flag and joins the thread
without a timeout: because the default backup checks the stop flag on
every progress step and each copy has a wall-clock deadline, the
thread always terminates, so no backup write can happen after
``close()`` returns -- call ``close()`` before the owning data service
closes. An injected ``backup_fn`` is called as ``backup_fn(data_dir,
backup_dir, now=<clock>, stop_event=<threading.Event>)`` and is
expected to honour the stop event the same way.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Union
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

HELSENKI_TZ = ZoneInfo("Europe/Helsinki")

PUMPS_DB = "pumps.db"
PRICES_DB = "prices.db"
COMPLETE_MARKER = ".complete"
TMP_SUFFIX = ".tmp"

# Long waits are executed in chunks no larger than this so that an
# injected wait() stays responsive and close() can stop the loop thread
# within a bounded time.
MAX_WAIT_CHUNK = 60.0

# Default wall-clock budget for copying one database. Checked on every
# progress step of sqlite3.Connection.backup, so it also bounds the
# busy/locked retry loop.
DEFAULT_COPY_TIMEOUT = 30.0


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def helsinki_day(now_dt: datetime) -> date:
    """The Helsinki calendar date (YYYY-MM-DD) of an aware datetime."""
    return now_dt.astimezone(HELSENKI_TZ).date()


@dataclass(frozen=True)
class BackupResult:
    """The outcome of one ``backup_once`` call.

    day: the Helsinki date (ISO) the attempt targeted, or None when the
        attempt could not even determine one (crash).
    backed_up: True when this call wrote (or re-wrote) at least one
        snapshot and the day is now complete.
    skipped: True when the day already carried its completion marker and
        nothing was written.
    files: snapshot basenames present/finished for this day after the
        call (may be partial after a failure; empty when skipped).
    error: a human-readable failure reason, or None on success/skip.
    """

    day: Union[str, None]
    backed_up: bool
    skipped: bool
    files: tuple
    error: Union[str, None]

    @property
    def ok(self) -> bool:
        return self.backed_up or self.skipped


def _resolve_now(now) -> datetime:
    """Accept a datetime or a zero-argument callable returning one.

    Naive datetimes are taken to be UTC.
    """
    if callable(now):
        now = now()
    if not isinstance(now, datetime):
        raise TypeError("now must be a datetime or a callable returning one")
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now


class _CopyAborted(Exception):
    """The SQLite copy was cancelled or ran out of wall-clock budget."""


def _copy_sqlite(source: Path, destination: Path, *,
                 stop_event: Union[threading.Event, None] = None,
                 timeout: float = DEFAULT_COPY_TIMEOUT) -> None:
    """Consistently copy one SQLite database using the backup API.

    Raises _CopyAborted (or sqlite3.OperationalError) when the copy is
    cancelled, exceeds its wall-clock budget, or SQLite reports an
    error; the destination is then left partially written.
    """
    if destination.exists():
        destination.unlink()
    # URI form with mode=ro: sqlite3 never creates the source and the
    # percent-encoding from Path.as_uri keeps odd path names intact.
    read_conn = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True,
                                timeout=0.1)
    started = time.monotonic()
    try:
        write_conn = sqlite3.connect(str(destination))
        try:
            def _check(_rc, _remaining, _pagecount):
                if stop_event is not None and stop_event.is_set():
                    raise _CopyAborted("backup cancelled during shutdown")
                if timeout is not None and \
                        time.monotonic() - started > timeout:
                    raise _CopyAborted(
                        f"the copy exceeded its {timeout:.0f}s budget")
            # The progress callback runs after every step attempt of the
            # backup API -- including the busy/locked retries CPython
            # would otherwise loop in forever -- so it is the only place
            # Python code can interrupt the copy.
            _check(None, None, None)
            read_conn.backup(write_conn, pages=128, progress=_check, sleep=0.05)
        finally:
            write_conn.close()
    finally:
        read_conn.close()


def _atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + TMP_SUFFIX)
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _cleanup_temp_partials(day_dir: Path) -> None:
    for tmp in day_dir.glob("*" + TMP_SUFFIX):
        try:
            tmp.unlink()
        except OSError:
            pass


def backup_once(data_dir: str | Path, backup_dir: str | Path, *,
                now=utc_now,
                stop_event: Union[threading.Event, None] = None,
                copy_timeout: float = DEFAULT_COPY_TIMEOUT) -> BackupResult:
    """Take (or recognize) the Helsinki-day backup.

    data_dir: directory holding pumps.db (required) and, optionally,
        prices.db.
    backup_dir: directory that will contain one subdirectory per
        Helsinki day.
    now: a tz-aware (or naive = UTC) datetime, or a zero-argument
        callable returning one.
    stop_event: optional threading.Event checked during every SQLite
        copy step; when set, the attempt fails with a cancel error.
    copy_timeout: wall-clock seconds allowed per database copy (default
        30); checked on every copy step.

    Returns a BackupResult; raises only for programmer errors (bad
    argument types). Backup failures -- including a missing pumps.db --
    are reported through the result, never as exceptions.
    """
    data = Path(data_dir)
    destination_root = Path(backup_dir)
    now_dt = _resolve_now(now)
    day = helsinki_day(now_dt).isoformat()
    day_dir = destination_root / day

    pumps = data / PUMPS_DB
    if not pumps.is_file():
        return BackupResult(
            day=day, backed_up=False, skipped=False, files=(),
            error=f"{PUMPS_DB} is missing from {data}")

    if (day_dir / COMPLETE_MARKER).is_file():
        # The day is already complete (this process or a previous run):
        # restarts and same-day retries skip it and touch nothing.
        return BackupResult(day=day, backed_up=False, skipped=True,
                            files=(), error=None)

    targets = [pumps]
    prices = data / PRICES_DB
    if prices.is_file():
        # Optional: a missing prices.db is skipped, not an error.
        targets.append(prices)

    written_names: list[str] = []
    try:
        day_dir.mkdir(parents=True, exist_ok=True)
        _cleanup_temp_partials(day_dir)
        for source in targets:
            tmp = day_dir / (source.name + TMP_SUFFIX)
            _copy_sqlite(source, tmp, stop_event=stop_event,
                         timeout=copy_timeout)
            os.replace(tmp, day_dir / source.name)
            written_names.append(source.name)
        _atomic_write_text(
            day_dir / COMPLETE_MARKER,
            f"{now_dt.isoformat()} " + " ".join(written_names) + "\n",
        )
    except _CopyAborted as err:
        _cleanup_temp_partials(day_dir)
        return BackupResult(
            day=day, backed_up=False, skipped=False,
            files=tuple(source.name for source in targets
                        if (day_dir / source.name).is_file()),
            error=str(err))
    except Exception as err:
        _cleanup_temp_partials(day_dir)
        return BackupResult(
            day=day, backed_up=False, skipped=False,
            files=tuple(source.name for source in targets
                        if (day_dir / source.name).is_file()),
            error=f"the backup failed: {err}")

    return BackupResult(day=day, backed_up=True, skipped=False,
                        files=tuple(written_names), error=None)


class BackupLoop:
    """Threaded daily backup: first run at start, then once per Helsinki
    date, retrying failures every ``retry_interval`` seconds.

    Parameters mirror ``backup_once`` (which receives its stop event);
    additionally:
    wait: callable(seconds) used for all sleeping; the default is the
        stop event's ``wait()``, so ``close()`` wakes the thread
        immediately. An injected wait is always called with chunks of at
        most MAX_WAIT_CHUNK seconds.
    retry_interval: seconds between failed attempts (default 300.0).
    backup_fn: the function to run (default backup_once); it is called
        as ``backup_fn(data_dir, backup_dir, now=<clock>,
        stop_event=<threading.Event>)`` -- injectable so tests can count
        or force attempts, but it is expected to honour the stop event.
    """

    def __init__(self, data_dir: str | Path, backup_dir: str | Path, *,
                 now=utc_now, wait: Union[Callable[[float], None], None] = None,
                 retry_interval: float = 300.0,
                 backup_fn: Union[Callable[..., BackupResult], None] = None):
        self._data_dir = data_dir
        self._backup_dir = backup_dir
        self._now = now if callable(now) else (lambda: now)
        self._custom_wait = wait
        self._retry_interval = float(retry_interval)
        self._backup_fn = backup_fn if backup_fn is not None else backup_once
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._started = False
        self._last_result: BackupResult | None = None

    # -- lifecycle --

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, name="pump-backup", daemon=True)
            self._thread.start()

    def close(self) -> None:
        """Stop the loop and join its thread.

        Sets the stop flag and waits for the thread to exit. Because the
        default backup checks the stop flag on every progress step (and
        each copy has a wall-clock deadline), the thread always
        terminates, so no backup write can happen after close()
        returns. Call this before the owning data service closes.
        """
        with self._lock:
            if not self._started:
                return
            self._started = False
            thread = self._thread
            self._thread = None
        self._stop.set()
        if thread is not None:
            thread.join()

    @property
    def last_result(self) -> Union[BackupResult, None]:
        with self._lock:
            return self._last_result

    # -- internals --

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                result = self._backup_fn(
                    self._data_dir, self._backup_dir, now=self._now,
                    stop_event=self._stop)
            except Exception as err:
                result = BackupResult(
                    None, False, False, (),
                    f"the backup attempt crashed: {err}")
            with self._lock:
                previous, self._last_result = self._last_result, result
            # A missing backup drive fails every retry: log each new error once.
            if result.error and (previous is None or previous.error != result.error):
                log.warning("Backup %s failed: %s", result.day, result.error)
            elif result.backed_up:
                log.info("Backup %s written: %s", result.day, ", ".join(result.files))
            if result.ok:
                self._wait_until_next_day(result.day)
            else:
                self._sleep(self._retry_interval)

    def _wait(self, seconds: float) -> None:
        if self._custom_wait is not None:
            self._custom_wait(seconds)
        else:
            # The stop event's wait: close() wakes it immediately.
            self._stop.wait(seconds)

    def _sleep(self, seconds: float) -> None:
        """Sleep ``seconds`` (chunked) unless the loop is stopped."""
        remaining = max(0.0, float(seconds))
        while remaining > 0.0 and not self._stop.is_set():
            chunk = min(remaining, MAX_WAIT_CHUNK)
            self._wait(chunk)
            remaining -= chunk

    def _wait_until_next_day(self, completed_day: str) -> None:
        """Sleep until the Helsinki date changes or the loop is stopped.

        The starting date is captured once, outside the loop: the wait
        ends as soon as the Helsinki date differs from it, so a real
        rollover triggers the next day's backup instead of sleeping for
        the whole day again.
        """
        start_day = date.fromisoformat(completed_day)
        while not self._stop.is_set():
            if helsinki_day(_resolve_now(self._now())) != start_day:
                return  # the Helsinki date changed: run tomorrow's backup
            now_dt = _resolve_now(self._now())
            day = helsinki_day(now_dt)
            next_midnight = (
                datetime(day.year, day.month, day.day,
                         tzinfo=HELSENKI_TZ) + timedelta(days=1))
            remaining = (next_midnight - now_dt).total_seconds()
            if remaining <= 0.0:
                return  # the clock jumped ahead of the target midnight
            self._wait(min(remaining, MAX_WAIT_CHUNK))


# -- verify and restore (card H1.4) --

def _integrity(path: Path) -> str:
    """PRAGMA integrity_check of one database file: "ok" or the first problem."""
    try:
        conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)
        try:
            return str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        finally:
            conn.close()
    except sqlite3.Error as err:
        return f"error: {err}"


def verify_backup(day_dir: str | Path) -> dict:
    """Integrity-check every .db file in a backup day (or data) directory.

    Returns {file name: "ok" or a problem text}. A missing pumps.db is
    reported under its name, since a restore without it is useless.
    """
    folder = Path(day_dir)
    results = {path.name: _integrity(path) for path in sorted(folder.glob("*.db"))}
    if PUMPS_DB not in results:
        results[PUMPS_DB] = "missing"
    return results


def restore_backup(day_dir: str | Path, data_dir: str | Path, *,
                   force: bool = False) -> dict:
    """Copy a verified backup day into the data directory and verify the copy.

    Stop the service first. Refuses a backup that fails verification, and
    existing database files unless ``force``. Returns the verify results of
    the restored files; raises ValueError on refusal.
    """
    source, target = Path(day_dir), Path(data_dir)
    before = verify_backup(source)
    if any(result != "ok" for result in before.values()):
        raise ValueError("The backup failed verification: " + ", ".join(
            f"{name}: {result}" for name, result in before.items() if result != "ok"))
    names = [name for name in before]
    target.mkdir(parents=True, exist_ok=True)
    existing = [name for name in names if any(
        (target / (name + suffix)).exists() for suffix in ("", "-wal", "-shm"))]
    if existing and not force:
        raise ValueError("The data directory already has " + ", ".join(existing)
                         + "; use --force to replace it")
    for name in names:
        # A stale WAL from the old database would corrupt the restored one.
        for suffix in ("-wal", "-shm"):
            (target / (name + suffix)).unlink(missing_ok=True)
        tmp = target / (name + TMP_SUFFIX)
        shutil.copyfile(source / name, tmp)
        os.replace(tmp, target / name)
    return verify_backup(target)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify or restore a Villa Kuppis backup day.")
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("verify", help="run PRAGMA integrity_check on a folder")
    verify.add_argument("folder")
    restore = commands.add_parser("restore", help="copy a backup day into a data folder")
    restore.add_argument("backup_day_dir")
    restore.add_argument("data_dir")
    restore.add_argument("--force", action="store_true",
                         help="replace existing database files")
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            results = verify_backup(args.folder)
        else:
            results = restore_backup(args.backup_day_dir, args.data_dir, force=args.force)
    except ValueError as err:
        print(err)
        return 1
    for name, result in results.items():
        print(f"{name}: {result}")
    return 0 if all(result == "ok" for result in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
