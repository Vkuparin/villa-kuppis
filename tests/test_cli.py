"""CLI: help offline, --data-dir required, demo in a temp dir, refresh."""

import contextlib
import io
import json
import sqlite3
import tempfile
import urllib.error
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

from common import FIXTURES, NORMAL_DAY, elering_payload
from villa_kuppis import __main__ as cli
import unittest


def run_main(argv):
    """Run cli.main, capturing stdout/stderr; SystemExit becomes the code."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(argv)
        except SystemExit as exc:
            code = exc.code
    return code, out.getvalue(), err.getvalue()


NETWORK_GUARD = mock.patch(
    "villa_kuppis.prices.default_transport",
    side_effect=AssertionError("network access attempted"),
)


class TestHelp(unittest.TestCase):
    def test_help_exits_zero_and_never_touches_network(self):
        with NETWORK_GUARD:
            code, out, _ = run_main(["--help"])
        assert code == 0
        for word in ("--data-dir", "import-fixture", "demo", "refresh",
                     "schedule"):
            assert word in out

    def test_subcommand_help_also_offline(self):
        with NETWORK_GUARD:
            code, out, _ = run_main(["--data-dir", "x", "demo", "--help"])
        assert code == 0
        assert "--fixture" in out

    def test_version_offline(self):
        code, out, _ = run_main(["--version"])
        assert code == 0
        assert "villa_kuppis" in out

    def test_no_subcommand_exits_two(self):
        code, _, _ = run_main(["--data-dir", "x"])
        assert code == 2


class TestDataDir(unittest.TestCase):
    def test_missing_data_dir_rejected(self):
        code, _, err = run_main(["demo"])
        assert code == 2
        assert "--data-dir" in err

    def test_bad_date_rejected(self):
        code, _, err = run_main(["--data-dir", "x", "refresh",
                                 "--date", "not-a-date"])
        assert code == 2
        assert "YYYY-MM-DD" in err


class TestDemo(unittest.TestCase):
    def test_demo_offline_in_temp_dir(self):
        with NETWORK_GUARD:
            with tempfile.TemporaryDirectory() as tmp:
                code, out, err = run_main(["--data-dir", tmp, "demo"])
        assert code == 0, err
        assert "offline" in out
        assert "elering-2026-10-20.json" in out
        for room in ("living_room", "guest_room", "office"):
            assert f"Room {room}:" in out
        assert "2026-10-20" in out

    def test_demo_twice_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            code1, out1, _ = run_main(["--data-dir", tmp, "demo"])
            code2, out2, _ = run_main(["--data-dir", tmp, "demo"])
        assert code1 == code2 == 0
        assert out1.splitlines()[3] == out2.splitlines()[3]  # same day line


class TestImportFixtureCli(unittest.TestCase):
    def test_import_normal_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = run_main(
                ["--data-dir", tmp, "import-fixture",
                 str(FIXTURES / "elering-2026-10-20.json")])
        assert code == 0, err
        assert "96" in out
        assert "2026-10-20" in out

    def test_import_dst_fixtures(self):
        for name, count in (("elering-2026-03-29.json", "92"),
                            ("elering-2026-10-25.json", "100")):
            with tempfile.TemporaryDirectory() as tmp:
                code, out, err = run_main(["--data-dir", tmp,
                                           "import-fixture",
                                           str(FIXTURES / name)])
            assert code == 0, err
            assert count in out

    def test_import_with_wrong_date_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = run_main(["--data-dir", tmp, "import-fixture",
                                       str(FIXTURES / "elering-2026-10-20.json"),
                                       "--date", "2026-10-21"])
        assert code == 1
        assert "error:" in err

    def test_import_missing_file_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = run_main(["--data-dir", tmp, "import-fixture",
                                       str(FIXTURES / "nope.json")])
        assert code == 1
        assert "error:" in err


class TestRefreshCli(unittest.TestCase):
    def test_refresh_with_injected_default_transport(self):
        tomorrow = date(2026, 10, 20)
        transport = mock.MagicMock(return_value=elering_payload(tomorrow))
        with mock.patch("villa_kuppis.prices.default_transport", transport):
            with tempfile.TemporaryDirectory() as tmp:
                code, out, err = run_main(["--data-dir", tmp, "refresh",
                                           "--date", "2026-10-20"])
        assert code == 0, err
        assert "Refreshed 2026-10-20" in out
        assert transport.call_count == 1

    def test_refresh_with_settings_prints_plan(self):
        tomorrow = date(2026, 10, 20)
        transport = mock.MagicMock(return_value=elering_payload(tomorrow))
        with mock.patch("villa_kuppis.prices.default_transport", transport):
            with tempfile.TemporaryDirectory() as tmp:
                code, out, err = run_main(["--data-dir", tmp, "refresh",
                                           "--date", "2026-10-20",
                                           "--settings",
                                           str(FIXTURES /
                                               "settings-example.json")])
        assert code == 0, err
        assert "Room living_room:" in out

    def test_refresh_failure_clear_error_exit_one(self):
        transport = mock.MagicMock(
            side_effect=urllib.error.URLError("nope"))
        with mock.patch("villa_kuppis.prices.default_transport", transport):
            with tempfile.TemporaryDirectory() as tmp:
                code, out, err = run_main(["--data-dir", tmp, "refresh",
                                           "--date", "2026-10-20"])
        assert code == 1
        assert "error:" in err
        assert "nope" in err


class TestScheduleCli(unittest.TestCase):
    """schedule: validates before arming, one attempt per day, plan after
    success, clean stop, no auto-retry."""

    SETTINGS = {
        "site": {"base_temperature_c": 19.0,
                 "rules": [{"kind": "heat", "threshold_c_kwh": 2.0,
                            "target_c": 21.0}]},
        "rooms": {"living_room": {}},
    }

    # 2026-10-19 12:00 UTC = 15:00 EEST: after 14:25, so the attempt fires.
    NOW = datetime(2026, 10, 19, 12, 0, 0, tzinfo=timezone.utc)

    def _write_settings(self, tmp: str, payload: object) -> str:
        path = Path(tmp) / "settings.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return str(path)

    def _interrupting_sleep(self, sleeps: list):
        def sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) == 2:
                raise KeyboardInterrupt
        return sleep

    def test_schedule_success_fetches_once_and_prints_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._write_settings(tmp, self.SETTINGS)
            sleeps: list = []
            transport = mock.MagicMock(
                return_value=elering_payload(NORMAL_DAY))
            with mock.patch("villa_kuppis.service.utc_now",
                            return_value=self.NOW), \
                 mock.patch.object(cli, "_sleep",
                                   side_effect=self._interrupting_sleep(
                                       sleeps)), \
                 mock.patch("villa_kuppis.prices.default_transport",
                            transport):
                code, out, err = run_main(["--data-dir", tmp, "schedule",
                                           "--poll-seconds", "5",
                                           "--settings", settings])
            assert code == 0, err
            assert "Scheduler armed" in out
            assert "96 periods stored" in out
            assert "Room living_room: 96 of 96" in out, "plan after success"
            assert "Stopped." in out
            assert len(sleeps) == 2
            assert transport.call_count == 1, "exactly one fetch per local day"
            assert (Path(tmp) / "prices.db").exists()

    def test_schedule_failure_no_auto_retry_and_clean_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            sleeps: list = []
            transport = mock.MagicMock(
                side_effect=urllib.error.URLError("boom"))
            with mock.patch("villa_kuppis.service.utc_now",
                            return_value=self.NOW), \
                 mock.patch.object(cli, "_sleep",
                                   side_effect=self._interrupting_sleep(
                                       sleeps)), \
                 mock.patch("villa_kuppis.prices.default_transport",
                            transport):
                code, out, err = run_main(["--data-dir", tmp, "schedule",
                                           "--poll-seconds", "5"])
            assert code == 0, err
            assert "error: fetch failed" in out
            assert "Room" not in out, "no plan after a failed attempt"
            assert transport.call_count == 1, "no automatic retry"
            # NOTE: a sqlite3 connection is not closed by its context
            # manager; close it before the temp dir goes away (Windows).
            conn = sqlite3.connect(Path(tmp) / "prices.db")
            try:
                row = conn.execute(
                    "SELECT outcome FROM scheduled_attempt "
                    "WHERE local_date = '2026-10-19'").fetchone()
            finally:
                conn.close()
            assert row is not None and row[0] == "error"

    def test_poll_seconds_validated_before_arming(self):
        for bad in ("0", "nan", "-5"):
            with tempfile.TemporaryDirectory() as tmp:
                code, out, err = run_main(["--data-dir", tmp, "schedule",
                                           "--poll-seconds", bad])
            assert code == 2, (bad, err)
            assert "--poll-seconds" in err
            assert not (Path(tmp) / "prices.db").exists(), \
                "service must not be constructed before validation"

    def test_bad_settings_fail_before_http_and_arming(self):
        mixed = {"site": {"base_temperature_c": 19.0,
                          "rules": [{"kind": "heat",
                                     "threshold_c_kwh": 2.0,
                                     "target_c": 21.0},
                                    {"kind": "cool",
                                     "threshold_c_kwh": 8.0,
                                     "target_c": 17.0}]},
                 "rooms": {"r": {}}}
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._write_settings(tmp, mixed)
            with mock.patch("villa_kuppis.prices.default_transport") \
                    as transport:
                code, out, err = run_main(["--data-dir", tmp, "schedule",
                                           "--settings", settings])
            assert code == 2, err
            assert "mix" in err
            transport.assert_not_called()
            assert not (Path(tmp) / "prices.db").exists()

    def test_refresh_storage_failure_is_clean_error(self):
        with mock.patch("villa_kuppis.prices.default_transport",
                        mock.MagicMock(
                            return_value=elering_payload(NORMAL_DAY))), \
             mock.patch("villa_kuppis.storage.PriceStore.replace_day",
                        side_effect=sqlite3.OperationalError(
                            "disk full")):
            with tempfile.TemporaryDirectory() as tmp:
                code, out, err = run_main(["--data-dir", tmp, "refresh",
                                           "--date", "2026-10-20"])
        assert code == 1
        assert "error:" in err
        assert "disk full" in err
        assert "Traceback" not in err

    def test_schedule_state_failure_is_clean_error(self):
        with mock.patch("villa_kuppis.service.utc_now",
                        return_value=self.NOW), \
             mock.patch("villa_kuppis.storage.PriceStore.mark_attempt",
                        side_effect=sqlite3.OperationalError("db locked")):
            with tempfile.TemporaryDirectory() as tmp:
                code, out, err = run_main(["--data-dir", tmp, "schedule",
                                           "--poll-seconds", "5"])
        assert code == 1
        assert "scheduler state unavailable" in err
        assert "db locked" in err
        assert "Traceback" not in err
