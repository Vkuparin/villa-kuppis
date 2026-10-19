"""Command-line entry point for the spot-price experiment.

Every command requires an explicit ``--data-dir``; there is no default
production path. ``--help`` (and any argparse failure) exits before the
service is constructed, so it never touches the network.

Commands:

* ``import-fixture FILE [--date YYYY-MM-DD]``  validate and store a saved
  Elering-format fixture (offline).
* ``demo [--fixture FILE] [--settings FILE]``  offline demo: import the
  saved synthetic fixture (default: the one in tests/fixtures), load the
  example synthetic settings and print the plan.
* ``refresh [--date YYYY-MM-DD] [--settings FILE]``  explicit on-demand
  fetch of one day (default: tomorrow) from Elering over HTTP; prints the
  plan afterwards when settings are given.
* ``schedule [--settings FILE] [--poll-seconds N]``  opt-in foreground
  loop; attempts tomorrow once per local day at/after 14:25 Helsinki and,
  when settings are given, prints the next day's plan after a successful
  attempt. Settings and --poll-seconds are validated before anything is
  armed, so the command never arms against a broken settings file.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
import time as _time
from datetime import datetime, timedelta
from pathlib import Path

from . import __version__
from .planning import RoomPlan, normalize_settings
from .prices import HELSINKI, PriceFetchError, PriceValidationError
from .service import SpotPriceService

#: Saved synthetic fixtures shipped with the checkout (tests/fixtures).
DEFAULT_FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
DEFAULT_FIXTURE = DEFAULT_FIXTURES / "elering-2026-10-20.json"
DEFAULT_SETTINGS = DEFAULT_FIXTURES / "settings-example.json"


def _parse_date(value: str) -> object:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid date {value!r}, want YYYY-MM-DD"
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="villa_kuppis",
        description=(
            "Villa Kuppis spot-price experiment: Elering day-ahead FI "
            "prices, fixed-threshold plans, once-per-day scheduler. "
            "All commands require an explicit --data-dir."
        ),
    )
    parser.add_argument("--version", action="version",
                        version=f"villa_kuppis {__version__}")
    parser.add_argument(
        "--data-dir",
        help="explicit data directory (required; nothing is written to a "
             "default location)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser(
        "import-fixture",
        help="validate and store a saved Elering-format fixture (offline)",
    )
    p.add_argument("fixture", help="path to the fixture JSON file")
    p.add_argument("--date", type=_parse_date, default=None,
                   help="Helsinki day the fixture covers (default: derived "
                        "from the first interval)")

    p = sub.add_parser(
        "demo",
        help="offline demo: import the saved fixture, print the plan",
    )
    p.add_argument("--fixture", default=None,
                   help=f"fixture file (default: {DEFAULT_FIXTURE.name})")
    p.add_argument("--settings", default=None,
                   help=f"settings file (default: {DEFAULT_SETTINGS.name})")

    p = sub.add_parser(
        "refresh",
        help="explicit on-demand fetch of one day from Elering",
    )
    p.add_argument("--date", type=_parse_date, default=None,
                   help="Helsinki day to fetch (default: tomorrow)")
    p.add_argument("--settings", default=None,
                   help="print the plan afterwards with these settings")

    p = sub.add_parser(
        "schedule",
        help="opt-in foreground loop: attempt tomorrow once per local day "
             "at/after 14:25 Europe/Helsinki",
    )
    p.add_argument("--poll-seconds", type=float, default=60.0,
                   help="check interval in seconds (default: 60)")
    p.add_argument("--settings", default=None,
                   help="print the plan after each successful attempt")
    return parser


def _load_settings(path: str | Path) -> object:
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def _load_validated_settings(path: str | Path | None) -> object | None:
    """Read and validate a settings file, or return None when omitted.

    Validation happens BEFORE any HTTP or scheduler arming so a broken
    settings file can never follow a half-done refresh.
    """
    if path is None:
        return None
    settings = _load_settings(path)
    normalize_settings(settings)
    return settings


def _sleep(seconds: float) -> None:
    """Sleep between scheduler polls (module seam so tests can speed it up
    or interrupt the loop without real wall time)."""
    _time.sleep(seconds)


def _print_plan(day, plans: dict[str, list[RoomPlan]]) -> None:
    print()
    for room, rows in plans.items():
        on_threshold = [r for r in rows if r.reason == "threshold"]
        print(f"Room {room}: {len(on_threshold)} of {len(rows)} periods on "
              f"threshold, {len(rows) - len(on_threshold)} on base")
        for row in on_threshold:
            when = datetime.fromtimestamp(row.ts_utc, tz=HELSINKI)
            print(f"  {when:%Y-%m-%d %H:%M} E  {row.temperature_c} C  "
                  f"price {row.price_c_kwh} < {row.matched_threshold_c_kwh}")


def _cmd_import_fixture(args: argparse.Namespace) -> int:
    with SpotPriceService(args.data_dir) as service:
        try:
            result = service.import_fixture(args.fixture, day=args.date)
        except (OSError, ValueError, PriceValidationError,
                sqlite3.Error) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    print(f"Stored {result.interval_count} periods for {result.day.isoformat()} "
          f"(retrieved {result.retrieved_at_utc})")
    return 0


def _cmd_demo(args: argparse.Namespace) -> int:
    fixture = Path(args.fixture) if args.fixture else DEFAULT_FIXTURE
    settings_path = Path(args.settings) if args.settings else DEFAULT_SETTINGS
    try:
        settings = _load_validated_settings(settings_path)
        with SpotPriceService(args.data_dir) as service:
            result = service.import_fixture(fixture)
            plans = service.get_plan(result.day, settings)
            stored = service.stored_intervals(result.day) or []
    except (OSError, ValueError, PriceValidationError,
            sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print("Villa Kuppis spot-price demo (offline)")
    print(f"Data dir:  {Path(args.data_dir).resolve()}")
    print(f"Day:       {result.day.isoformat()} (Europe/Helsinki, "
          f"{result.interval_count} periods)")
    print(f"Fixture:   {fixture}")
    print(f"Settings:  {settings_path}")
    if stored:
        prices = [s.price_c_kwh for s in stored]
        total = sum(prices)
        print("Prices c/kWh: min "
              f"{min(prices)} / max {max(prices)} / avg "
              f"{total / len(prices)}")
    _print_plan(result.day, plans)
    return 0


def _cmd_refresh(args: argparse.Namespace) -> int:
    # Settings are read and validated BEFORE any HTTP or storage work.
    try:
        settings_obj = _load_validated_settings(args.settings)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    with SpotPriceService(args.data_dir) as service:
        try:
            result = service.refresh(day=args.date)
        except (PriceFetchError, PriceValidationError,
                sqlite3.Error) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"Refreshed {result.day.isoformat()}: "
              f"{result.interval_count} periods stored "
              f"(retrieved {result.retrieved_at_utc})")
        if settings_obj is not None:
            try:
                plans = service.get_plan(result.day, settings_obj)
            except (LookupError, ValueError, sqlite3.Error) as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
            _print_plan(result.day, plans)
    return 0


def _cmd_schedule(args: argparse.Namespace) -> int:
    # Validate the poll interval BEFORE the service is constructed or the
    # loop is armed: nothing touches the network or the DB on bad input.
    if not math.isfinite(args.poll_seconds) or args.poll_seconds <= 0:
        print("error: --poll-seconds must be a positive finite number",
              file=sys.stderr)
        return 2
    # Validate settings before arming (see _load_validated_settings).
    try:
        settings_obj = _load_validated_settings(args.settings)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    service = SpotPriceService(args.data_dir)
    try:
        print("Scheduler armed: one attempt per local day at/after "
              "14:25 Europe/Helsinki; Ctrl+C to stop.")
        last_status: str | None = None
        while True:
            try:
                outcome = service.schedule_once()
            except sqlite3.Error as exc:
                # Scheduler state is unreadable; nothing can be done
                # safely. Fail clean instead of looping or tracebacks.
                print(f"error: scheduler state unavailable: {exc}",
                      file=sys.stderr)
                return 1
            if outcome.status != last_status:
                stamp = service.now().astimezone(HELSINKI)
                line = f"[{stamp:%Y-%m-%d %H:%M:%S} E] {outcome.status}"
                if outcome.detail:
                    line += f": {outcome.detail}"
                print(line)
                last_status = outcome.status
            if outcome.status == "ok" and settings_obj is not None:
                target = outcome.local_date + timedelta(days=1)
                try:
                    plans = service.get_plan(target, settings_obj)
                    _print_plan(target, plans)
                except (LookupError, ValueError, sqlite3.Error) as exc:
                    print(f"error: could not plan {target.isoformat()}: "
                          f"{exc}", file=sys.stderr)
            _sleep(args.poll_seconds)
    except KeyboardInterrupt:
        print("Stopped.")
        return 0
    finally:
        service.close()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.data_dir:
        print("error: --data-dir is required (no default production path)",
              file=sys.stderr)
        return 2
    if args.command == "import-fixture":
        return _cmd_import_fixture(args)
    if args.command == "demo":
        return _cmd_demo(args)
    if args.command == "refresh":
        return _cmd_refresh(args)
    if args.command == "schedule":
        return _cmd_schedule(args)
    build_parser().print_help()  # pragma: no cover (parser enforces)
    return 2  # pragma: no cover


if __name__ == "__main__":
    sys.exit(main())
