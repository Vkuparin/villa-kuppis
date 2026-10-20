"""Wires prices, storage and planning together.

The service is the only component that knows about the data directory, the
HTTP transport and the clock. All three are injectable, so the whole flow
(fixture import, refresh, plan recomputation, scheduling) is testable
offline with a temporary SQLite file and a fake clock.

Scheduling (docs/spot-price.md): the PC fetches today's prices whenever
they are missing and tomorrow's at or after 14:25 Europe/Helsinki when they
are missing. A stored day is never refetched automatically. The attempt for
a target day is saved BEFORE the HTTP request and the outcome AFTER it; a
failed or interrupted ('running') attempt is retried, but never sooner than
RETRY_SECONDS after the previous attempt for that same day, so a failure or
a crash cannot cause a request loop. Manual refresh is independent of this
state.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable, Sequence

from .planning import RoomPlan, Period, plan_day
from .prices import (DEFAULT_TIMEOUT_SECONDS, HELSINKI, PriceFetchError,
                     PriceValidationError, Transport, fetch_day,
                     validate_day)
from .storage import DB_FILENAME, PriceStore, StoredInterval, utc_now

#: Local time from which tomorrow's prices are fetched.
SCHEDULE_LOCAL_TIME = time(14, 25)
#: Minimum time between automatic attempts for the same target day.
RETRY_SECONDS = 15 * 60


@dataclass(frozen=True)
class FixtureImport:
    """Result of importing one saved Elering-format fixture file."""

    day: date
    interval_count: int
    retrieved_at_utc: str


@dataclass(frozen=True)
class RefreshResult:
    """Result of one successful on-demand refresh."""

    day: date
    interval_count: int
    retrieved_at_utc: str


@dataclass(frozen=True)
class DayFetch:
    """Result of one day in a manual fetch of the missing days.

    status: stored (already there, not fetched) | fetched |
    not_published (the source has no complete day yet) | error.
    """

    day: date
    status: str
    interval_count: int = 0
    detail: str = ""


@dataclass(frozen=True)
class ScheduleOutcome:
    """Result of one scheduler check.

    status: not_yet (tomorrow not due, nothing else missing) | stored
    (nothing missing) | waiting (a failed attempt is inside the retry
    delay) | ok | error. target_day is the day the attempt or wait is for.
    detail carries the human-readable error message for 'error'.
    """

    local_date: date
    status: str
    detail: str = ""
    target_day: date | None = None


def fixture_day(payload: bytes) -> date:
    """Helsinki calendar day of the first interval in an Elering body."""
    try:
        body = json.loads(payload)
        first = body["data"]["fi"][0]
        ts = first["timestamp"]
    except (json.JSONDecodeError, UnicodeDecodeError, KeyError, IndexError,
            TypeError) as exc:
        raise ValueError(
            "fixture is not an Elering response with data.fi entries"
        ) from exc
    if isinstance(ts, bool) or not isinstance(ts, int):
        raise ValueError(f"fixture timestamp must be unix seconds: {ts!r}")
    return datetime.fromtimestamp(ts, tz=HELSINKI).date()


class SpotPriceService:
    """One data directory's worth of spot-price behaviour."""

    def __init__(self, data_dir: str | Path,
                 transport: Transport | None = None,
                 now: Callable[[], datetime] | None = None,
                 timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.store = PriceStore(self.data_dir / DB_FILENAME)
        self._transport = transport
        self._timeout = timeout
        self._now = now or utc_now

    def close(self) -> None:
        self.store.close()

    def __enter__(self) -> "SpotPriceService":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def now(self) -> datetime:
        """The service clock (injectable; UTC-aware expected)."""
        return self._now()

    def tomorrow(self) -> date:
        """Tomorrow as a Europe/Helsinki calendar day."""
        return self.now().astimezone(HELSINKI).date() + timedelta(days=1)

    # -- fixture import / refresh -------------------------------------------

    def import_fixture(self, path: str | Path,
                       day: date | None = None) -> FixtureImport:
        """Validate and store a saved Elering-format fixture file.

        ``day`` defaults to the Helsinki day of the fixture's first
        interval. The stored day is replaced atomically.
        """
        payload = Path(path).read_bytes()
        day = day if day is not None else fixture_day(payload)
        intervals = validate_day(payload, day)
        retrieved = self._now()
        self.store.replace_day(day.isoformat(), intervals, retrieved)
        return FixtureImport(day, len(intervals), retrieved.isoformat())

    def refresh(self, day: date | None = None) -> RefreshResult:
        """Fetch one day from Elering, validate, store, ready to re-plan.

        Raises PriceFetchError/PriceValidationError on fetch or validation
        failure and sqlite3.Error on storage failure; the previously stored
        day (if any) is left untouched (replacement is atomic).
        """
        day = day if day is not None else self.tomorrow()
        intervals = fetch_day(day, transport=self._transport,
                              timeout=self._timeout)
        retrieved = self._now()
        self.store.replace_day(day.isoformat(), intervals, retrieved)
        return RefreshResult(day, len(intervals), retrieved.isoformat())

    def fetch_missing(self) -> list[DayFetch]:
        """Fetch today's and tomorrow's prices when they are not stored.

        The manual counterpart of the scheduler: no time-of-day gate and no
        attempt record. A stored day is left alone. Tomorrow's prices are
        normally published around 14:00, so an incomplete tomorrow is
        reported as not_published instead of an error.
        """
        today = self.now().astimezone(HELSINKI).date()
        results = []
        for day in (today, today + timedelta(days=1)):
            stored = self.stored_intervals(day)
            if stored is not None:
                results.append(DayFetch(day, "stored", len(stored)))
                continue
            try:
                refreshed = self.refresh(day=day)
            except PriceValidationError as exc:
                if day > today and "missing interval" in str(exc):
                    results.append(DayFetch(day, "not_published"))
                else:
                    results.append(DayFetch(day, "error", detail=str(exc)))
            except (PriceFetchError, sqlite3.Error) as exc:
                results.append(DayFetch(day, "error", detail=str(exc)))
            else:
                results.append(DayFetch(day, "fetched",
                                        refreshed.interval_count))
        return results

    # -- planning -----------------------------------------------------------

    def stored_intervals(self, day: date) -> list[StoredInterval] | None:
        return self.store.stored_day(day.isoformat())

    def get_plan(self, day: date, settings: object) -> dict[str, list[RoomPlan]]:
        """Plan for a stored day under the given settings.

        Reads the currently stored intervals, so a successful refresh is
        automatically reflected in the next plan (recomputation).
        """
        stored = self.stored_intervals(day)
        if stored is None:
            raise LookupError(f"no prices stored for {day.isoformat()}")
        periods = [Period(s.ts_utc, s.price_c_kwh) for s in stored]
        return plan_day(periods, settings)

    # -- scheduler ------------------------------------------------------------

    def schedule_once(self) -> ScheduleOutcome:
        """One scheduler check against the injected clock.

        Fetches today's prices when missing, and tomorrow's when missing at
        or after 14:25 Helsinki. At most one fetch per call. The attempt is
        recorded before the HTTP request and the outcome after it; a failed
        or interrupted attempt is retried after RETRY_SECONDS.
        """
        now = self.now()
        local = now.astimezone(HELSINKI)
        local_date = local.date()
        targets = [local_date]
        if local.time() >= SCHEDULE_LOCAL_TIME:
            targets.append(local_date + timedelta(days=1))
        waiting: ScheduleOutcome | None = None
        for target_day in targets:
            if self.store.stored_day(target_day.isoformat()) is not None:
                continue
            key = target_day.isoformat()
            previous = self.store.attempt(key)
            if previous is not None:
                due = (datetime.fromisoformat(previous.attempted_at_utc)
                       + timedelta(seconds=RETRY_SECONDS))
                if now < due:
                    waiting = waiting or ScheduleOutcome(
                        local_date, "waiting",
                        f"{key}: previous attempt {previous.outcome} at "
                        f"{previous.attempted_at_utc} UTC", target_day)
                    continue
            self.store.mark_attempt(key, now)
            try:
                result = self.refresh(day=target_day)
            except (PriceFetchError, PriceValidationError,
                    sqlite3.Error) as exc:
                try:
                    self.store.finish_attempt(key, "error")
                except sqlite3.Error:
                    # The store is too broken to record the outcome; the
                    # 'running' row stays, visible as an interrupted attempt.
                    pass
                return ScheduleOutcome(local_date, "error", str(exc),
                                       target_day)
            self.store.finish_attempt(key, "ok")
            return ScheduleOutcome(
                local_date, "ok",
                f"{result.day.isoformat()}: {result.interval_count} periods "
                f"stored (retrieved {result.retrieved_at_utc})", target_day)
        if waiting is not None:
            return waiting
        if len(targets) == 1:
            return ScheduleOutcome(local_date, "not_yet")
        return ScheduleOutcome(local_date, "stored")
