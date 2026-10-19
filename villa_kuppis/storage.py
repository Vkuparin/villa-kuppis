"""SQLite storage for validated price days and scheduler state.

Layout under the explicit data directory:

* ``prices.db`` with ``price_interval`` rows (UTC unix seconds, the raw
  EUR/MWh price as exact decimal text, retrieval time in UTC) and a
  ``scheduled_attempt`` row per local date.

Day replacement is atomic: the delete + insert happens in one transaction,
so a storage failure mid-replacement rolls back and the previously stored
complete day stays untouched.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Sequence

from .prices import PriceInterval

DB_FILENAME = "prices.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS price_interval (
    ts_utc INTEGER PRIMARY KEY,
    day TEXT NOT NULL,
    price_raw_eur_mwh TEXT NOT NULL,
    retrieved_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_price_interval_day ON price_interval(day);
CREATE TABLE IF NOT EXISTS scheduled_attempt (
    local_date TEXT PRIMARY KEY,
    attempted_at_utc TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('running', 'ok', 'error'))
);
"""


def utc_now() -> datetime:
    """Timezone-aware UTC now (the default clock)."""
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class StoredInterval:
    """A stored interval, already converted to c/kWh."""

    ts_utc: int
    price_c_kwh: Decimal
    retrieved_at_utc: str


@dataclass(frozen=True)
class Attempt:
    """One scheduled attempt record. outcome: running | ok | error."""

    local_date: str
    attempted_at_utc: str
    outcome: str


class PriceStore:
    """A SQLite-backed price store for one data directory."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None: autocommit mode; transactions are managed
        # explicitly below so replacement atomicity is unambiguous.
        self._conn = sqlite3.connect(self.path, isolation_level=None)
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "PriceStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- prices -----------------------------------------------------------

    def stored_day(self, day: str) -> list[StoredInterval] | None:
        """All stored intervals for a Helsinki day ('YYYY-MM-DD'), or None."""
        rows = self._conn.execute(
            "SELECT ts_utc, price_raw_eur_mwh, retrieved_at_utc "
            "FROM price_interval WHERE day = ? ORDER BY ts_utc",
            (day,),
        ).fetchall()
        if not rows:
            return None
        return [StoredInterval(ts, Decimal(raw) / Decimal(10), retrieved)
                for ts, raw, retrieved in rows]

    def replace_day(self, day: str, intervals: Sequence[PriceInterval],
                    retrieved_at_utc: datetime) -> None:
        """Atomically replace the full stored day.

        Any error (constraint violation, disk failure, ...) rolls the
        transaction back, leaving the previous complete day intact.
        """
        retrieved = retrieved_at_utc.isoformat()
        params = [(iv.ts_utc, day, str(iv.raw_price_eur_mwh), retrieved)
                  for iv in intervals]
        cur = self._conn.cursor()
        try:
            cur.execute("BEGIN IMMEDIATE")
            cur.execute("DELETE FROM price_interval WHERE day = ?", (day,))
            cur.executemany(
                "INSERT INTO price_interval "
                "(ts_utc, day, price_raw_eur_mwh, retrieved_at_utc) "
                "VALUES (?, ?, ?, ?)",
                params,
            )
            cur.execute("COMMIT")
        except BaseException:
            cur.execute("ROLLBACK")
            raise

    # -- scheduler state ----------------------------------------------------

    def attempt(self, local_date: str) -> Attempt | None:
        row = self._conn.execute(
            "SELECT local_date, attempted_at_utc, outcome "
            "FROM scheduled_attempt WHERE local_date = ?",
            (local_date,),
        ).fetchone()
        return Attempt(*row) if row else None

    def mark_attempt(self, local_date: str, attempted_at_utc: datetime) -> None:
        """Record an attempt BEFORE the HTTP request.

        Raises sqlite3.IntegrityError if the date already has an attempt
        (the caller must check first; the primary key is the backstop).
        """
        self._conn.execute(
            "INSERT INTO scheduled_attempt "
            "(local_date, attempted_at_utc, outcome) VALUES (?, ?, 'running')",
            (local_date, attempted_at_utc.isoformat()),
        )

    def finish_attempt(self, local_date: str, outcome: str) -> None:
        """Record the outcome AFTER the HTTP request (ok | error)."""
        if outcome not in ("ok", "error"):
            raise ValueError(f"outcome must be 'ok' or 'error', got {outcome!r}")
        self._conn.execute(
            "UPDATE scheduled_attempt SET outcome = ? WHERE local_date = ?",
            (outcome, local_date),
        )
