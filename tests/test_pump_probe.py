"""Offline unit tests for villa_kuppis.pump_probe (card F2.1).

No network, no accounts, no real devices: the live client boundary is an
in-memory FakeClient built on the real aiomelcloudhome models, credentials,
TTY and waits are injected, and subprocess checks prove --help and the demo
run offline against the installed package.
"""

import asyncio
import getpass
import json
import subprocess
import sys
import unittest
import warnings
from pathlib import Path

from aiomelcloudhome import exceptions as mel_exc
from aiomelcloudhome.models.ata import ATAUnitControl

from villa_kuppis import pump_probe
from villa_kuppis.pump_probe import FakeClient, _unit_payload

REPO_ROOT = Path(__file__).resolve().parent.parent
UP_ID = "LN25UP-UNIT-ID"
UP_NAME = "Upstairs bedroom"


def _up_payload(**over):
    base = dict(mode="Cool", power="True", set_temp="23", room="26", fan="Auto")
    base.update(over)
    return _unit_payload(UP_ID, UP_NAME, **base)


def _run_live(argv, fake=None, *, inputs=None, getpass_results=("owner", "s3cret"),
              getpass_side_effect=None, custom_getpass=None, isatty=True,
              put_errors=(), put_stale=(), mutates=(), sleep_side_effects=(),
              context_error=None, context_error_after=None, close_error=None):
    """Drive pump_probe.main in-process with spies. Returns (rc, output, fake, calls)."""
    if fake is None:
        fake = FakeClient([_up_payload()], put_errors=put_errors,
                          put_stale=put_stale, mutates=mutates,
                          context_error=context_error,
                          context_error_after=context_error_after,
                          close_error=close_error)
    out = []
    calls = {"getpass": 0, "factory": 0, "input": 0}
    input_queue = list(inputs or [])
    sleeps = list(sleep_side_effects)

    def input_fn(prompt):
        calls["input"] += 1
        if not input_queue:
            raise EOFError
        return input_queue.pop(0)

    if custom_getpass is None:
        def getpass_fn(prompt):
            calls["getpass"] += 1
            if getpass_side_effect is not None:
                getpass_side_effect()
            return getpass_results[calls["getpass"] - 1]
    else:
        def getpass_fn(prompt):
            calls["getpass"] += 1
            return custom_getpass(prompt)

    def factory(username, password):
        calls["factory"] += 1
        return fake, fake.close

    async def sleep_fn(delay):
        if sleeps:
            effect = sleeps.pop(0)
            if isinstance(effect, BaseException):
                raise effect
            if callable(effect):
                result = effect(delay)
                if asyncio.iscoroutine(result):
                    await result

    try:
        rc = pump_probe.main(
            argv, client_factory=factory, getpass_fn=getpass_fn, input_fn=input_fn,
            sleep_fn=sleep_fn, isatty_fn=lambda: isatty, print_fn=out.append,
        )
    except SystemExit as exc:  # argparse usage errors (e.g. --live demo)
        rc = exc.code
    return rc, "\n".join(out), fake, calls


def _set_temperature_of(call):
    return call[1]["set_temperature"]


def _only_set_temperature(call):
    kwargs = call[1]
    non_null = {k: v for k, v in kwargs.items() if v is not None}
    return list(non_null) == ["set_temperature"]


class CliShape(unittest.TestCase):
    def test_help_is_fully_offline(self):
        proc = subprocess.run(
            [sys.executable, "-m", "villa_kuppis.pump_probe", "--help"],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0)
        for token in ("demo", "read", "test-setpoint", "--live"):
            self.assertIn(token, proc.stdout)

    def test_default_command_is_offline_demo(self):
        proc = subprocess.run(
            [sys.executable, "-m", "villa_kuppis.pump_probe"],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Offline demo", proc.stdout)
        self.assertIn("change confirmed by read-back", proc.stdout)
        self.assertIn("restore confirmed by read-back", proc.stdout)
        self.assertIn("no other controls changed", proc.stdout)
        self.assertNotIn("RESTORATION UNCONFIRMED", proc.stdout)
        self.assertNotIn("FAILED", proc.stdout)


class LiveGating(unittest.TestCase):
    def test_gating_refusals(self):
        for command in ("read", "test-setpoint"):
            with self.subTest(command=command):
                rc, out, fake, calls = _run_live([command])
                self.assertNotEqual(rc, 0)
                self.assertIn("--live", out)
                self.assertEqual(calls["getpass"], 0)
                self.assertEqual(calls["factory"], 0)
                self.assertEqual(fake.closed, 0)
        with self.subTest(live_demo=True):
            rc, out, fake, calls = _run_live(["--live", "demo"])
            self.assertNotEqual(rc, 0)
            self.assertEqual(calls["factory"], 0)
        with self.subTest(non_tty=True):
            rc, out, fake, calls = _run_live(["--live", "read"], isatty=False)
            self.assertNotEqual(rc, 0)
            self.assertIn("interactive terminal", out)
            self.assertEqual(calls["getpass"], 0)
            self.assertEqual(calls["factory"], 0)


class CredentialSafety(unittest.TestCase):
    def test_getpass_warning_aborts_before_insecure_read(self):
        """A GetPassWarning must abort BEFORE any insecure read, the second
        prompt, or client construction."""
        unsafe_reads = []

        def custom_getpass(prompt):
            warnings.warn(getpass.GetPassWarning("cannot disable echo"))
            unsafe_reads.append(prompt)  # would-be insecure echoed read
            return "leaked"

        rc, out, fake, calls = _run_live(
            ["--live", "read"], custom_getpass=custom_getpass
        )
        self.assertNotEqual(rc, 0)
        self.assertIn("securely", out)
        self.assertEqual(unsafe_reads, [])   # side effect never ran
        self.assertEqual(calls["getpass"], 1)  # second prompt never ran
        self.assertEqual(calls["factory"], 0)
        self.assertEqual(fake.closed, 0)


class ReadDisplay(unittest.TestCase):
    def test_read_shows_cloud_reported_state(self):
        rc, out, fake, _calls = _run_live(["--live", "read"])
        self.assertEqual(rc, 0)
        for token in (
            "[1]", UP_NAME, "Cool", "on", "23.0 C", "26.0 C", "Auto",
            "heat 10.0-30.0 C", "cool/dry 16.0-30.0 C", "auto 10.0-30.0 C",
            "half-degree steps", "cloud reported",
        ):
            self.assertIn(token, out)
        self.assertNotIn(UP_ID, out)
        lowered = out.lower()
        for banned in ("online", "fresh", "physical"):
            self.assertNotIn(banned, lowered)
        self.assertEqual(fake.closed, 1)


class Refusals(unittest.TestCase):
    def test_refusals_make_zero_control_calls(self):
        caps_no_cool = {
            "numberOfFanSpeeds": 5,
            "minTempHeat": 10, "maxTempHeat": 30,
            "hasHalfDegreeIncrements": True,
        }
        caps_tight_heat = {
            "numberOfFanSpeeds": 5,
            "minTempHeat": 23, "maxTempHeat": 23,
            "minTempCoolDry": 16, "maxTempCoolDry": 30,
            "minTempAutomatic": 10, "maxTempAutomatic": 30,
            "hasHalfDegreeIncrements": True,
        }
        cases = [
            ("identity_denied", None, ["1", "no"], "Identity not confirmed"),
            ("test_word_denied", None, ["1", "yes", "no"], "Not confirmed"),
            ("fan_mode", _up_payload(mode="Fan"), ["1", "yes"], "Fan mode"),
            ("error_state", _up_payload(error="True"), ["1", "yes"], "reports an error"),
            ("missing_setpoint", _up_payload(set_temp="N/A"), ["1", "yes"],
             "no usable setpoint"),
            ("missing_bounds",
             _unit_payload(UP_ID, UP_NAME, mode="Cool", power="True", set_temp="23",
                           room="26", fan="Auto", caps=caps_no_cool),
             ["1", "yes"], "no usable temperature bounds"),
            # Original outside bounds must be refused BEFORE any PUT, not
            # discovered when the out-of-range restore is attempted.
            ("original_below_bounds", _up_payload(set_temp="15"), ["1", "yes"],
             "outside the reported"),
            ("original_above_bounds", _up_payload(set_temp="31"), ["1", "yes"],
             "outside the reported"),
            ("no_step_in_bounds",
             _unit_payload(UP_ID, UP_NAME, mode="Heat", power="True", set_temp="23",
                           room="26", fan="Auto", caps=caps_tight_heat),
             ["1", "yes"], "No +/-1 step fits"),
        ]
        for name, payload, inputs, expect in cases:
            with self.subTest(case=name):
                fake = FakeClient([payload]) if payload is not None \
                    else FakeClient([_up_payload()])
                rc, out, fake, _calls = _run_live(
                    ["--live", "test-setpoint"], fake=fake, inputs=inputs
                )
                self.assertNotEqual(rc, 0)
                self.assertIn(expect, out)
                self.assertEqual(fake.control_calls, [])


class SupervisedFlows(unittest.TestCase):
    def test_happy_path_write_verify_restore(self):
        rc, out, fake, _calls = _run_live(
            ["--live", "test-setpoint"], inputs=["1", "yes", "TEST"]
        )
        self.assertEqual(rc, 0)
        self.assertEqual(len(fake.control_calls), 2)
        first, second = fake.control_calls
        self.assertEqual(first[0], UP_ID)
        self.assertEqual(second[0], UP_ID)
        self.assertEqual(_set_temperature_of(first), 24.0)
        self.assertEqual(_set_temperature_of(second), 23.0)
        self.assertTrue(_only_set_temperature(first))
        self.assertTrue(_only_set_temperature(second))
        self.assertIn("setpoint now: 23.0 C", out)
        self.assertIn("proposed:     24.0 C", out)
        self.assertIn("change confirmed by read-back", out)
        self.assertIn("restore confirmed by read-back", out)
        self.assertIn("no other controls changed", out)
        self.assertIn("Probe complete.", out)
        self.assertNotIn("RESTORATION UNCONFIRMED", out)
        self.assertEqual(fake.closed, 1)

    def test_first_put_applied_then_raised_restored(self):
        """Failure after the first write: original is restored and confirmed,
        and the exit code is a plain error (1), not unconfirmed."""
        rc, out, fake, _calls = _run_live(
            ["--live", "test-setpoint"], inputs=["1", "yes", "TEST"],
            put_errors=[mel_exc.MelCloudHomeConnectionError("boom")],
        )
        self.assertEqual(rc, 1)
        self.assertEqual(len(fake.control_calls), 2)
        self.assertEqual(_set_temperature_of(fake.control_calls[0]), 24.0)
        self.assertEqual(_set_temperature_of(fake.control_calls[1]), 23.0)
        self.assertIn("restored and confirmed by read-back", out)
        self.assertNotIn("RESTORATION UNCONFIRMED", out)

    def test_restore_put_failed_but_independent_get_confirms(self):
        """An applied-then-raised restore write is decided by the independent
        GET, not assumed failed: the original is read back, so the exit code
        is a plain error (1) with the confirmed-restore message, not 3."""
        boom = mel_exc.MelCloudHomeConnectionError("boom")
        rc, out, fake, _calls = _run_live(
            ["--live", "test-setpoint"], inputs=["1", "yes", "TEST"],
            put_errors=[boom, boom],
        )
        self.assertEqual(rc, 1)
        self.assertEqual(len(fake.control_calls), 2)
        self.assertEqual(_set_temperature_of(fake.control_calls[0]), 24.0)
        self.assertEqual(_set_temperature_of(fake.control_calls[1]), 23.0)
        self.assertIn("target write failed:", out)
        self.assertIn("restoration write failed:", out)
        self.assertIn("restored and confirmed by read-back", out)
        self.assertNotIn("RESTORATION UNCONFIRMED", out)

    def test_verification_never_confirms_still_restores(self):
        rc, out, fake, _calls = _run_live(
            ["--live", "test-setpoint"], inputs=["1", "yes", "TEST"],
            put_stale=[10, 0],
        )
        self.assertNotEqual(rc, 0)
        self.assertIn("not confirmed by read-back", out)
        self.assertEqual(len(fake.control_calls), 2)
        self.assertEqual(_set_temperature_of(fake.control_calls[1]), 23.0)
        self.assertIn("restored and confirmed by read-back", out)
        self.assertNotIn("RESTORATION UNCONFIRMED", out)

    def test_genuine_restore_failure_stays_unconfirmed(self):
        """Genuine restore failures are when the independent GET cannot
        confirm the original: the GET fails, or the original never reads
        back. Both stay unconfirmed (3), including when a teardown close
        error adds noise; no false probe success."""
        boom = mel_exc.MelCloudHomeConnectionError("boom")
        cases = [
            ("get_fails_during_restoration",
             dict(put_errors=[None, boom], context_error=boom,
                  context_error_after=3)),
            ("original_never_reads_back",
             dict(put_errors=[boom], put_stale=[0, 99])),
            ("close_error_must_not_mask",
             dict(put_errors=[boom], put_stale=[0, 99],
                  close_error=RuntimeError("session close broken"))),
        ]
        for name, kwargs in cases:
            with self.subTest(case=name):
                rc, out, fake, _calls = _run_live(
                    ["--live", "test-setpoint"], inputs=["1", "yes", "TEST"],
                    **kwargs,
                )
                self.assertEqual(rc, pump_probe.EXIT_RESTORE_UNCONFIRMED)
                self.assertIn("RESTORATION UNCONFIRMED", out)
                self.assertIn("MELCloud Home app", out)
                self.assertIn("23.0 C", out)
                self.assertNotIn("Probe complete.", out)
                self.assertNotIn("Traceback", out)
                if "close" in name:
                    self.assertNotIn("session close broken", out)

    def test_external_control_change_fails_test(self):
        """A non-setpoint control that drifts during the test must fail the
        probe (nonzero) even though the restore itself is confirmed, and only
        setpoint controls may ever be written."""
        def flip_power(unit_payload_dict):
            for setting in unit_payload_dict["settings"]:
                if setting["name"] == "Power":
                    setting["value"] = "False"

        rc, out, fake, _calls = _run_live(
            ["--live", "test-setpoint"], inputs=["1", "yes", "TEST"],
            mutates=[flip_power, None],
        )
        self.assertEqual(rc, 1)
        self.assertEqual(len(fake.control_calls), 2)
        for call in fake.control_calls:
            self.assertTrue(_only_set_temperature(call))
        self.assertIn("note: power changed from on to off", out)
        self.assertIn("not written back", out)
        self.assertIn("FAILED", out)
        self.assertIn("restore confirmed by read-back", out)
        self.assertNotIn("Probe complete.", out)
        self.assertNotIn("RESTORATION UNCONFIRMED", out)

    def test_single_interrupt_during_verification_restores_first(self):
        rc, out, fake, _calls = _run_live(
            ["--live", "test-setpoint"], inputs=["1", "yes", "TEST"],
            put_stale=[1, 1, 0],
            sleep_side_effects=[asyncio.CancelledError()],
        )
        self.assertNotEqual(rc, 0)
        self.assertNotIn("Probe complete.", out)
        self.assertIn("Interrupted", out)
        self.assertIn("restored and confirmed by read-back", out)
        self.assertNotIn("RESTORATION UNCONFIRMED", out)
        self.assertEqual(len(fake.control_calls), 2)
        self.assertEqual(_set_temperature_of(fake.control_calls[1]), 23.0)

    def test_double_interrupt_during_cleanup_is_unconfirmed(self):
        """A second interrupt while the shielded restore task is running must
        stop that task deterministically (no dangling task, no session closed
        under it) and report unconfirmed, not success."""
        async def self_cancel(_delay):
            # Simulate a second Ctrl+C arriving while the cleanup task is
            # mid-flight: cancel the cleanup task itself.
            current = asyncio.current_task()
            assert current is not None
            current.cancel()
            await asyncio.sleep(3600)  # never returns; the cancel lands here

        rc, out, fake, _calls = _run_live(
            ["--live", "test-setpoint"], inputs=["1", "yes", "TEST"],
            put_stale=[1, 1],
            sleep_side_effects=[asyncio.CancelledError(), self_cancel],
        )
        self.assertEqual(rc, pump_probe.EXIT_RESTORE_UNCONFIRMED)
        self.assertIn("RESTORATION UNCONFIRMED", out)
        self.assertNotIn("Probe complete.", out)
        self.assertNotIn("Traceback", out)
        self.assertEqual(len(fake.control_calls), 2)
        self.assertEqual(_set_temperature_of(fake.control_calls[1]), 23.0)


class Sanitization(unittest.TestCase):
    def test_secret_bearing_error_is_never_leaked(self):
        secret = "TOKEN-SECRET-abc123"
        fake = FakeClient(
            [_up_payload()],
            context_error=RuntimeError(f"Bearer {secret} user=ACCT-42"),
        )
        rc, out, _fake, _calls = _run_live(["--live", "read"], fake=fake)
        self.assertNotEqual(rc, 0)
        self.assertNotIn(secret, out)
        self.assertNotIn("ACCT-42", out)
        self.assertNotIn("Traceback", out)
        self.assertNotIn("RuntimeError", out)

    def test_eof_aborts_cleanly_without_traceback(self):
        rc, out, fake, _calls = _run_live(["--live", "test-setpoint"], inputs=[])
        self.assertNotEqual(rc, 0)
        self.assertIn("Input ended", out)
        self.assertNotIn("Traceback", out)
        self.assertEqual(fake.control_calls, [])


class DiagnosticsSanitization(unittest.TestCase):
    """Phase diagnostics: bounded known causes only, never exception text.

    A fake secret rides in the exception's cause/body/URL; only the phase
    and a numeric status or the sanitized category may reach the output.
    """

    def _chained_http_error(self, status, secret):
        """Library connection error chained from an aiohttp response error
        whose URL and message carry the secret."""
        import aiohttp
        from aiohttp import RequestInfo
        from multidict import CIMultiDict

        url = f"https://mobile.bff.melcloudhome.com/monitor/ataunit/{UP_ID}?token=***"
        info = RequestInfo(url, "PUT", CIMultiDict({"Authorization": f"Bearer {secret}"}))
        http_err = aiohttp.ClientResponseError(info, (), status=status,
                                               message=f"boom {secret}")
        try:
            raise mel_exc.MelCloudHomeConnectionError("Connection error")
        except mel_exc.MelCloudHomeConnectionError as lib_err:
            lib_err.__cause__ = http_err
            return lib_err

    def test_http_error_reports_phase_and_status_only(self):
        secret = "TOKEN-SECRET-abc123"
        lib_err = self._chained_http_error(401, secret)
        message = pump_probe._operation_message("target write", lib_err)
        self.assertEqual(message, "target write failed: HTTP 401")
        self.assertNotIn(secret, message)
        self.assertNotIn(UP_ID, message)
        self.assertNotIn("mobile.bff", message)

    def test_json_error_reports_invalid_json_not_body(self):
        secret = "TOKEN-SECRET-abc123"
        err = json.JSONDecodeError(f"Expecting value: {secret}", "Bearer " + secret, 0)
        message = pump_probe._operation_message("restoration read-back", err)
        self.assertEqual(message, "restoration read-back failed: invalid JSON response")
        self.assertNotIn(secret, message)

    def test_failed_write_reports_phase_and_still_restores(self):
        """In-flow: a secret-bearing chained HTTP error on the target write
        prints phase + status only, and the restore still confirms."""
        secret = "TOKEN-SECRET-abc123"
        lib_err = self._chained_http_error(503, secret)
        rc, out, fake, _calls = _run_live(
            ["--live", "test-setpoint"], inputs=["1", "yes", "TEST"],
            put_errors=[lib_err],
        )
        self.assertEqual(rc, 1)
        self.assertEqual(len(fake.control_calls), 2)
        self.assertIn("target write failed: HTTP 503", out)
        self.assertIn("restored and confirmed by read-back", out)
        self.assertNotIn(secret, out)
        self.assertNotIn(UP_ID, out)
        self.assertNotIn("mobile.bff", out)
        self.assertNotIn("Traceback", out)

    def test_unknown_error_keeps_sanitized_category(self):
        secret = "TOKEN-SECRET-abc123"
        fake = FakeClient(
            [_up_payload()],
            context_error=RuntimeError(f"Bearer {secret} user=ACCT-42"),
        )
        rc, out, _fake, _calls = _run_live(["--live", "read"], fake=fake)
        self.assertNotEqual(rc, 0)
        self.assertIn("A MELCloud Home error occurred.", out)
        self.assertNotIn(secret, out)
        self.assertNotIn("ACCT-42", out)
        self.assertNotIn("Traceback", out)


class DependencyHttpContract(unittest.TestCase):
    """Offline contract against the installed aiomelcloudhome dependency.

    A successful 200 whose body is not valid JSON surfaces as a raw
    json.JSONDecodeError from control_ata_unit: the dependency parses every
    body unless content_length is 0 or the status is 204/304, and
    JSONDecodeError is not among its retried transient failures. The
    outbound PUT carries the full payload, null fields included. No
    network: the session is a recording stub and the auth is a static
    token.
    """

    def test_200_with_unparseable_body_raises_jsondecodeerror(self):
        from aiomelcloudhome import MELCloudHome

        token = "static-test-token"
        seen = []

        class _FakeResponse:
            status = 200
            content_length = None  # unknown length: no empty-body shortcut

            def raise_for_status(self):
                return None

            async def text(self):
                return ""

            async def json(self, content_type=None):
                raise json.JSONDecodeError("Expecting value", "", 0)

        class _FakeSession:
            async def request(self, method, url, **kwargs):
                seen.append({"method": method, "url": str(url), **kwargs})
                return _FakeResponse()

            async def close(self):
                return None

        client = MELCloudHome(access_token=token, session=_FakeSession())
        with self.assertRaises(json.JSONDecodeError):
            asyncio.run(client.control_ata_unit("LN25-UNIT-42", set_temperature=24.0))

        self.assertEqual(len(seen), 1,
                         "JSONDecodeError is not a retried transient failure")
        request = seen[0]
        self.assertEqual(request["method"], "PUT")
        self.assertTrue(request["url"].endswith("/monitor/ataunit/LN25-UNIT-42"))
        self.assertEqual(request["headers"]["Authorization"], f"Bearer {token}")
        self.assertEqual(
            request["json"],
            {
                "power": None,
                "operationMode": None,
                "setTemperature": 24.0,
                "setFanSpeed": None,
                "vaneVerticalDirection": None,
                "vaneHorizontalDirection": None,
                "temperatureIncrementOverride": None,
                "inStandbyMode": None,
            },
        )


class PayloadContract(unittest.TestCase):
    def test_only_set_temperature_is_written(self):
        control = ATAUnitControl(set_temperature=23.5)
        payload = control.to_api_payload()
        non_null = {k: v for k, v in payload.items() if v is not None}
        self.assertEqual(non_null, {"setTemperature": 23.5})
        rc, _out, fake, _calls = _run_live(
            ["--live", "test-setpoint"], inputs=["1", "yes", "TEST"]
        )
        self.assertEqual(rc, 0)
        for call in fake.control_calls:
            self.assertTrue(_only_set_temperature(call),
                            f"unexpected non-null control args: {call[1]}")


if __name__ == "__main__":
    unittest.main()
