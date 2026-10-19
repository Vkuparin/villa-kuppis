"""Wires prices, storage and planning together.

The service is the only component that knows about the data directory, the
HTTP transport and the clock. All three are injectable, so the whole flow
(fixture import, refresh, plan recomputation, scheduling) is testable
offline with a temporary SQLite file and a fake clock.

Scheduling (docs/spot-price.md): the PC fetches tomorrow's prices once per
local day at or after 14:25 Europe/Helsinki. Starting the loop late catches
up. The scheduled-attempt date is saved BEFORE the HTTP request and the
outcome AFTER it, so a failure or a crash cannot cause a request loop: an
already-attempted date (ok, error or an interrupted 'running') is never
retried automatically. Manual refresh is independent of this state.
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

#: Local time from which the once-per-day attempt is allowed.
SCHEDULE_LOCAL_TIME = time(14, 25)


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
class ScheduleOutcome:
    """Result of one scheduler check.

    status: not_yet | ok | error | already_ok | already_error |
    already_running. detail carries the human-readable error message for
    'error'.
    """

    local_date: date
    status: str
    detail: str = ""


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

        Attempts tomorrow's prices once per local date at/after 14:25
        Helsinki. The attempt date is recorded before the HTTP request and
        the outcome after it; any date with an existing attempt record is
        left alone (no automatic retry of failures or interrupted runs).
        """
        local = self.now().astimezone(HELSINKI)
        local_date = local.date()
        if local.time() < SCHEDULE_LOCAL_TIME:
            return ScheduleOutcome(local_date, "not_yet")
        existing = self.store.attempt(local_date.isoformat())
        if existing is not None:
            return ScheduleOutcome(
                local_date, f"already_{existing.outcome}",
                f"attempted at {existing.attempted_at_utc} UTC",
            )
        target_day = local_date + timedelta(days=1)
        self.store.mark_attempt(local_date.isoformat(), self.now())
        try:
            result = self.refresh(day=target_day)
        except (PriceFetchError, PriceValidationError,
                sqlite3.Error) as exc:
            try:
                self.store.finish_attempt(local_date.isoformat(), "error")
            except sqlite3.Error:
                # The store is too broken to record the outcome; the
                # 'running' row stays, visible as an interrupted attempt.
                pass
            return ScheduleOutcome(local_date, "error", str(exc))
        self.store.finish_attempt(local_date.isoformat(), "ok")
        return ScheduleOutcome(
            local_date, "ok",
            f"{result.day.isoformat()}: {result.interval_count} periods "
            f"stored (retrieved {result.retrieved_at_utc})",
        )
