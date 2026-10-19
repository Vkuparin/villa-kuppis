"""Supervised first probe for MELCloud Home air-to-air units (card F2.1).

Offline-first: importing this module, ``--help`` and the ``demo`` command never
touch the network. The demo runs against an in-memory fake client built on the
real library models.

Live commands (``read``, ``test-setpoint``) require ``--live`` and a real
interactive terminal. Credentials are read with getpass (username and
password) and are never taken from arguments, environment, files, or
persisted anywhere.

Safety model for ``test-setpoint``:
  * one unit, chosen by session-local number, identity confirmed by the owner;
  * at most one degree moved, inside the unit's own reported bounds;
  * the exact word TEST is required immediately before the write;
  * the write is verified by fresh read-backs, then the original setpoint is
    restored and verified the same way;
  * once the first write has been attempted, the original setpoint is
    restored again on every exit path (failure, failed verification, Ctrl+C);
  * if the restore cannot be confirmed, the exit code is non-zero and a
    prominent RESTORATION UNCONFIRMED notice tells the owner what to check in
    the MELCloud Home app.

Read values are always labelled "cloud reported": the model carries no
physical-presence or timestamp data, so this probe never claims a unit is
physically online or that a value is fresh.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import math
import sys
import warnings

# --- library log suppression -------------------------------------------------
# The aiomelcloudhome package (parent logger) gets a NullHandler, does not
# propagate, and is set above CRITICAL, so neither its own records nor any
# child-logger records (aiomelcloudhome.websocket etc.) can reach the console
# or root handlers.
_LIB_LOGGER_NAME = "aiomelcloudhome"
_lib_logger = logging.getLogger(_LIB_LOGGER_NAME)
_lib_logger.addHandler(logging.NullHandler())
_lib_logger.propagate = False
_lib_logger.setLevel(logging.CRITICAL + 1)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_RESTORE_UNCONFIRMED = 3

DEFAULT_DEADLINE = 30.0
MAX_POLLS = 3
POLL_DELAY = 5.0

_CONTROL_FIELDS = (
    "power",
    "operation_mode",
    "set_fan_speed",
    "in_standby_mode",
    "vane_vertical_direction",
    "vane_horizontal_direction",
)


class _ProbeError(Exception):
    """Internal: the supervised flow failed in a known, reported way."""


# --- formatting helpers (no IDs, no raw objects) ------------------------------

def _fmt_temp(value):
    if value is None or not math.isfinite(value):
        return "not reported"
    return f"{value:.1f} C"


def _fmt_flag(value):
    if value is None:
        return "not reported"
    return "on" if value else "off"


def _fmt_enum(value):
    if value is None:
        return "not reported"
    return getattr(value, "value", str(value))


def _fmt_value(value):
    if isinstance(value, bool):
        return _fmt_flag(value)
    return _fmt_enum(value)


def _fmt_range(lo, hi):
    if lo is None or hi is None or not (math.isfinite(lo) and math.isfinite(hi)):
        return "not reported"
    return f"{lo:.1f}-{hi:.1f} C"


def _caps_line(caps):
    if caps is None:
        return "capability ranges: not reported"
    parts = [
        f"heat {_fmt_range(caps.min_temp_heat, caps.max_temp_heat)}",
        f"cool/dry {_fmt_range(caps.min_temp_cool, caps.max_temp_cool)}",
        f"auto {_fmt_range(caps.min_temp_auto, caps.max_temp_auto)}",
    ]
    if caps.has_half_degree_increments is True:
        parts.append("half-degree steps")
    elif caps.has_half_degree_increments is False:
        parts.append("whole-degree steps")
    return "capability ranges: " + ", ".join(parts)


# --- context helpers -----------------------------------------------------------

def collect_units(context):
    """All air-to-air units across buildings, in context order."""
    return [unit for building in context.buildings for unit in building.air_to_air_units]


def find_unit_by_id(context, unit_id):
    for unit in collect_units(context):
        if unit.id == unit_id:
            return unit
    return None


def _control_snapshot(unit):
    return {field: getattr(unit, field) for field in _CONTROL_FIELDS}


def _control_diffs(before, after):
    return [
        (field, before[field], after[field])
        for field in _CONTROL_FIELDS
        if before[field] != after[field]
    ]


def _bounds_for_mode(caps, mode):
    if caps is None or mode is None:
        return None, None
    if mode == "Heat":
        return caps.min_temp_heat, caps.max_temp_heat
    if mode in ("Cool", "Dry"):
        return caps.min_temp_cool, caps.max_temp_cool
    if mode == "Automatic":
        return caps.min_temp_auto, caps.max_temp_auto
    return None, None


def _choose_target(original, lo, hi):
    """original+1 if in bounds, else original-1, else None."""
    if lo <= original + 1.0 <= hi:
        return original + 1.0
    if lo <= original - 1.0 <= hi:
        return original - 1.0
    return None


# --- in-memory fake client (demo + tests) --------------------------------------

def _unit_payload(unit_id, name, *, mode="Cool", power="True", set_temp="23",
                  room="26", fan="Auto", standby="False", error="False",
                  caps=None):
    settings = [
        {"name": "Power", "value": power},
        {"name": "OperationMode", "value": mode},
        {"name": "SetTemperature", "value": set_temp},
        {"name": "RoomTemperature", "value": room},
        {"name": "SetFanSpeed", "value": fan},
        {"name": "InStandbyMode", "value": standby},
        {"name": "IsInError", "value": error},
    ]
    return {
        "id": unit_id,
        "givenDisplayName": name,
        "settings": settings,
        "capabilities": caps if caps is not None else _DEFAULT_CAPS,
    }


_DEFAULT_CAPS = {
    "numberOfFanSpeeds": 5,
    "minTempHeat": 10,
    "maxTempHeat": 30,
    "minTempCoolDry": 16,
    "maxTempCoolDry": 30,
    "minTempAutomatic": 10,
    "maxTempAutomatic": 30,
    "hasHalfDegreeIncrements": True,
}


def _context_payload(units_payloads):
    return {
        "buildings": [
            {"id": "B1", "name": "Home", "airToAirUnits": [dict(p) for p in units_payloads]}
        ]
    }


class FakeClient:
    """Offline stand-in for MELCloudHome, built on the real library models.

    Behavior is scriptable per control call:
      put_errors[i]  exception raised by the i-th control call (after applying)
      put_stale[i]   how many following get_context calls still return the
                     pre-write value (read-back lag); 0 = immediate
      mutates[i]     optional callable(unit_payload) run after the i-th apply
    """

    def __init__(self, payloads, *, put_errors=(), put_stale=(), mutates=(),
                 context_error=None, close_error=None):
        from aiomelcloudhome import UserContext

        self._user_context = UserContext
        self._units = {p["id"]: dict(p) for p in payloads}
        self.control_calls = []
        self._put_errors = list(put_errors)
        self._put_stale = list(put_stale)
        self._mutates = list(mutates)
        self._context_error = context_error
        self._close_error = close_error
        self._stale = 0
        self._stale_snapshot = None
        self.closed = 0

    def _context(self, units):
        return self._user_context.model_validate(_context_payload(units))

    async def get_context(self):
        if self._context_error is not None:
            raise self._context_error
        if self._stale:
            self._stale -= 1
            return self._context([dict(p) for p in self._stale_snapshot])
        return self._context(list(self._units.values()))

    async def control_ata_unit(self, unit_id, *, power=None, operation_mode=None,
                               set_temperature=None, set_fan_speed=None,
                               vane_vertical_direction=None, vane_horizontal_direction=None,
                               in_standby_mode=None):
        index = len(self.control_calls)
        self.control_calls.append((
            unit_id,
            {
                "power": power,
                "operation_mode": operation_mode,
                "set_temperature": set_temperature,
                "set_fan_speed": set_fan_speed,
                "vane_vertical_direction": vane_vertical_direction,
                "vane_horizontal_direction": vane_horizontal_direction,
                "in_standby_mode": in_standby_mode,
            },
        ))
        error = self._put_errors[index] if index < len(self._put_errors) else None
        stale = self._put_stale[index] if index < len(self._put_stale) else 0
        if set_temperature is not None and unit_id in self._units:
            before = [dict(p) for p in self._units.values()]
            settings = {s["name"]: s["value"] for s in self._units[unit_id]["settings"]}
            settings["SetTemperature"] = f"{set_temperature:.1f}"
            self._units[unit_id]["settings"] = [
                {"name": n, "value": v} for n, v in settings.items()
            ]
            if index < len(self._mutates) and self._mutates[index] is not None:
                self._mutates[index](self._units[unit_id])
            self._stale = stale
            self._stale_snapshot = before
        if error is not None:
            raise error

    async def close(self):
        self.closed += 1
        if self._close_error is not None:
            raise self._close_error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _demo_client():
    return FakeClient([
        _unit_payload("demo-unit-1", "Demo bedroom", mode="Cool",
                      power="True", set_temp="23", room="26", fan="Auto"),
    ])


# --- live plumbing --------------------------------------------------------------

def _default_client_factory(username, password):
    """Real live client: lazy import so --help/demo stay light and offline."""
    from aiohttp import ClientSession

    from aiomelcloudhome import MELCloudHome

    session = ClientSession()
    client = MELCloudHome(
        username=username,
        password=password,
        session=session,
        request_timeout=10.0,
    )
    return client, session.close


def _prompt_credentials(getpass_fn, print_fn):
    """Read username and password via getpass.

    Returns (username, password), or None when secure reading is impossible
    (GetPassWarning or empty input) - the caller must abort, never fall back
    to echoing. Each prompt is checked individually: the moment a
    GetPassWarning appears, the rest of the exchange is abandoned.
    """

    def _secure(prompt):
        try:
            with warnings.catch_warnings():
                # Convert GetPassWarning to an exception BEFORE the read is
                # attempted, so getpass's insecure fallback (echoed read of
                # the credential) can never run before we abort.
                warnings.simplefilter("error", category=getpass.GetPassWarning)
                return getpass_fn(prompt)
        except getpass.GetPassWarning:
            return None
        except EOFError:
            return None
        except Exception:
            return None

    username = _secure("MELCloud Home username: ")
    if username is None:
        return None
    password = _secure("MELCloud Home password: ")
    if password is None:
        return None
    if not username or not password:
        return None
    return username, password


def _generic_message(exc):
    """Map a library error to a safe, generic message (no raw exception text)."""
    try:
        from aiomelcloudhome import exceptions as _ex
    except ImportError:
        return "A MELCloud Home error occurred."
    if isinstance(exc, _ex.MelCloudHomeAuthenticationError):
        return "Authentication failed. Check the MELCloud Home credentials."
    if isinstance(exc, _ex.MelCloudHomeTimeoutError):
        return "A MELCloud Home request timed out."
    if isinstance(exc, _ex.MelCloudHomeNotFoundError):
        return "The requested unit was not found in MELCloud Home."
    if isinstance(exc, _ex.MelCloudHomeWebSocketError):
        return "The MELCloud Home stream is unavailable."
    if isinstance(exc, _ex.MelCloudHomeError):
        return "A MELCloud Home request failed. Check the connection and try again."
    if isinstance(exc, TimeoutError):
        return f"A MELCloud Home operation exceeded its {DEFAULT_DEADLINE:.0f} second deadline."
    return "A MELCloud Home error occurred."


def _mel_exceptions():
    try:
        from aiomelcloudhome import exceptions as _ex
        return (_ex.MelCloudHomeError, TimeoutError)
    except ImportError:
        return (TimeoutError,)


# --- commands --------------------------------------------------------------------

async def read_live(client, *, print_fn=print, deadline=DEFAULT_DEADLINE):
    context = await asyncio.wait_for(client.get_context(), timeout=deadline)
    units = collect_units(context)
    if not units:
        print_fn("No air-to-air units are listed in MELCloud Home.")
        return EXIT_ERROR
    print_fn("MELCloud Home air-to-air units:")
    for alias, unit in enumerate(units, start=1):
        print_fn(f"[{alias}] {unit.name or '(unnamed)'}")
        print_fn(f"     mode:      {_fmt_enum(unit.operation_mode)} (cloud reported)")
        print_fn(f"     power:     {_fmt_flag(unit.power)} (cloud reported)")
        print_fn(f"     setpoint:  {_fmt_temp(unit.set_temperature)} (cloud reported)")
        print_fn(f"     room temp: {_fmt_temp(unit.room_temperature)} (cloud reported)")
        print_fn(f"     fan:       {_fmt_enum(unit.set_fan_speed)} (cloud reported)")
        print_fn("     " + _caps_line(unit.capabilities))
    print_fn("All values are cloud reported state.")
    return EXIT_OK


async def _restore_once(client, unit_id, original, *, deadline, max_polls, poll_delay, sleep_fn, print_fn):
    """Best-effort restore of the original setpoint with read-back verification."""
    await asyncio.wait_for(
        client.control_ata_unit(unit_id, set_temperature=original), timeout=deadline
    )
    for attempt in range(1, max_polls + 1):
        context = await asyncio.wait_for(client.get_context(), timeout=deadline)
        unit = find_unit_by_id(context, unit_id)
        st = unit.set_temperature if unit is not None else None
        if unit is not None and st is not None and math.isfinite(st) and st == original:
            return True
        if attempt < max_polls:
            print_fn(f"  restore read-back {attempt}: not yet {original:.1f} C; waiting {poll_delay:.0f}s")
            await sleep_fn(poll_delay)
    return False


def _print_unconfirmed(print_fn, name, original):
    bar = "!" * 62
    print_fn(bar)
    print_fn("!! RESTORATION UNCONFIRMED".center(62))
    print_fn(bar)
    print_fn(f'The probe could not confirm that "{name}" is back at {original:.1f} C.')
    print_fn("Open the MELCloud Home app and set the unit back to "
             f"{original:.1f} C if it is not already there.")
    print_fn("Do not rely on the last read-back; it could not be verified.")


async def _supervised_write(client, *, unit_id, name, original, target, baseline,
                            sleep_fn, print_fn, deadline, max_polls, poll_delay):
    """Write target, verify, restore, verify - with guaranteed cleanup.

    Returns an exit code. The original setpoint is restored (again) in the
    finally block whenever the first write was attempted but the happy path
    did not finish, covering failures, failed verification and cancellation.
    """
    write_attempted = False
    restore_done = False
    interrupted = False
    result = EXIT_ERROR
    observed_diffs = []

    def _note_diffs(unit):
        """Record any non-setpoint control drifting from the baseline."""
        for field, before, after in _control_diffs(baseline, _control_snapshot(unit)):
            if not any(f == field for f, _b, _a in observed_diffs):
                observed_diffs.append((field, before, after))

    def _unswallow_cancel():
        # Let the cleanup result (restored or unconfirmed) survive to the exit
        # code instead of being re-raised by asyncio.run as a bare interrupt.
        current = asyncio.current_task()
        if current is not None and hasattr(current, "uncancel") and current.cancelling():
            current.uncancel()

    async def _read_unit():
        context = await asyncio.wait_for(client.get_context(), timeout=deadline)
        return find_unit_by_id(context, unit_id)

    async def _verify(expected):
        for attempt in range(1, max_polls + 1):
            unit = await _read_unit()
            st = unit.set_temperature if unit is not None else None
            if unit is not None and st is not None and math.isfinite(st) and st == expected:
                return unit
            if attempt < max_polls:
                print_fn(f"  read-back {attempt}: not yet {expected:.1f} C; waiting {poll_delay:.0f}s")
                await sleep_fn(poll_delay)
        return None

    try:
        print_fn(f"Writing {target:.1f} C ...")
        # Flag BEFORE awaiting: a timeout can happen after the write applied.
        write_attempted = True
        await asyncio.wait_for(
            client.control_ata_unit(unit_id, set_temperature=target), timeout=deadline
        )
        unit = await _verify(target)
        if unit is None:
            raise _ProbeError(
                f"the change to {target:.1f} C was not confirmed by read-back after {max_polls} attempts"
            )
        print_fn(f"  change confirmed by read-back: {target:.1f} C")
        _note_diffs(unit)
        print_fn(f"Restoring {original:.1f} C ...")
        await asyncio.wait_for(
            client.control_ata_unit(unit_id, set_temperature=original), timeout=deadline
        )
        final = await _verify(original)
        if final is None:
            raise _ProbeError(
                f"the restore to {original:.1f} C was not confirmed by read-back after {max_polls} attempts"
            )
        restore_done = True
        print_fn(f"  restore confirmed by read-back: {original:.1f} C")
        _note_diffs(final)
        if observed_diffs:
            for field, before, after in observed_diffs:
                print_fn(f"  note: {field} changed from {_fmt_value(before)} "
                         f"to {_fmt_value(after)} during the test "
                         "(not written back; check the MELCloud Home app)")
            print_fn("Another control changed during the test; the probe result is FAILED.")
        else:
            print_fn("  no other controls changed")
            print_fn("Probe complete.")
        if observed_diffs:
            # A drift that later returned to baseline still invalidates the
            # probe: something else touched the unit while we were.
            result = EXIT_ERROR
        else:
            result = EXIT_OK
    except asyncio.CancelledError:
        interrupted = True
        _unswallow_cancel()
    except _ProbeError as err:
        print_fn(f"Probe failed: {err}")
    except BaseException as err:  # library errors/timeouts: generic message only
        print_fn(_generic_message(err))
    finally:
        if write_attempted and not restore_done:
            print_fn("Cleaning up: restoring the original setpoint ...")
            ok = False
            try:
                task = asyncio.ensure_future(
                    _restore_once(
                        client, unit_id, original,
                        deadline=deadline, max_polls=max_polls,
                        poll_delay=poll_delay, sleep_fn=sleep_fn, print_fn=print_fn,
                    )
                )
                # Shield so a second interrupt during cleanup does not kill the
                # restore mid-flight; if one arrives, stop the task
                # deterministically before the session closes (never closing a
                # client/session under a task that still uses it) and report
                # unconfirmed.
                try:
                    ok = await asyncio.shield(task)
                except asyncio.CancelledError:
                    ok = False
                    _unswallow_cancel()
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            except Exception:
                ok = False
            if ok:
                print_fn(f"  original setpoint {original:.1f} C restored and confirmed by read-back")
            else:
                _print_unconfirmed(print_fn, name, original)
                result = EXIT_RESTORE_UNCONFIRMED
    if interrupted and result != EXIT_RESTORE_UNCONFIRMED:
        print_fn("Interrupted before the probe completed.")
    return result


async def test_setpoint_live(client, *, input_fn, sleep_fn, print_fn=print,
                             deadline=DEFAULT_DEADLINE, max_polls=MAX_POLLS,
                             poll_delay=POLL_DELAY):
    """Select a unit, confirm identity, probe one degree, restore, report."""

    def _ask(prompt):
        print_fn(prompt)
        return input_fn(prompt)

    context = await asyncio.wait_for(client.get_context(), timeout=deadline)
    units = collect_units(context)
    if not units:
        print_fn("No air-to-air units are listed in MELCloud Home.")
        return EXIT_ERROR
    for alias, unit in enumerate(units, start=1):
        print_fn(f"[{alias}] {unit.name or '(unnamed)'} - mode: {_fmt_enum(unit.operation_mode)}, "
                 f"power: {_fmt_flag(unit.power)}")
    print_fn("Select the unit to test (number):")
    unit = None
    while unit is None:
        raw = _ask("Unit number: ")
        try:
            number = int(raw.strip())
        except (TypeError, ValueError):
            print_fn("Enter a number from the list above.")
            continue
        if 1 <= number <= len(units):
            unit = units[number - 1]
        else:
            print_fn("Enter a number from the list above.")

    name = unit.name or "(unnamed)"
    if _ask(f'Is "{name}" the upstairs LN25 unit you want to test? [yes/no] ').strip().lower() != "yes":
        print_fn("Identity not confirmed; nothing was changed.")
        return EXIT_ERROR

    # Fresh context: locate the same unit by ID, not by list position.
    context2 = await asyncio.wait_for(client.get_context(), timeout=deadline)
    unit2 = find_unit_by_id(context2, unit.id)
    if unit2 is None:
        print_fn("The selected unit is no longer listed; aborting without changes.")
        return EXIT_ERROR

    if unit2.is_in_error is True:
        print_fn("The unit reports an error; refusing to test it.")
        return EXIT_ERROR
    mode = unit2.operation_mode
    if mode is None:
        print_fn("The unit reports no operation mode; refusing to test it.")
        return EXIT_ERROR
    if mode == "Fan":
        print_fn("The unit is in Fan mode; there is no temperature to test. Aborting.")
        return EXIT_ERROR
    original = unit2.set_temperature
    if original is None or not math.isfinite(original):
        print_fn("The unit reports no usable setpoint; refusing to test it.")
        return EXIT_ERROR
    lo, hi = _bounds_for_mode(unit2.capabilities, mode)
    if lo is None or hi is None or not (math.isfinite(lo) and math.isfinite(hi)) or lo > hi:
        print_fn(f"The unit reports no usable temperature bounds for {mode}; refusing to test it.")
        return EXIT_ERROR
    if not (lo <= original <= hi):
        print_fn(f"The reported setpoint is outside the reported {mode} bounds "
                 f"({_fmt_range(lo, hi)}); refusing to test it.")
        return EXIT_ERROR
    target = _choose_target(original, lo, hi)
    if target is None:
        print_fn("No +/-1 step fits inside the reported bounds; refusing to test it.")
        return EXIT_ERROR

    baseline = _control_snapshot(unit2)
    print_fn("")
    print_fn(f"Unit: {name}")
    print_fn(f"  mode:         {mode} (cloud reported)")
    print_fn(f"  power:        {_fmt_flag(unit2.power)} (cloud reported)")
    print_fn(f"  setpoint now: {original:.1f} C")
    print_fn(f"  proposed:     {target:.1f} C (reported bounds {lo:.1f}-{hi:.1f} C)")
    print_fn("Type TEST to write the change now; anything else aborts without writing.")
    if _ask("Confirm: ").strip() != "TEST":
        print_fn("Not confirmed; no change was made.")
        return EXIT_ERROR

    return await _supervised_write(
        client, unit_id=unit2.id, name=name, original=original, target=target,
        baseline=baseline, sleep_fn=sleep_fn, print_fn=print_fn,
        deadline=deadline, max_polls=max_polls, poll_delay=poll_delay,
    )


async def _demo(print_fn=print):
    print_fn("Offline demo: in-memory fake unit. No network, no prompts, no real waits.")
    fake = _demo_client()
    answers = iter(["1", "yes", "TEST"])

    def input_fn(prompt):
        try:
            return next(answers)
        except StopIteration:
            return "TEST"

    async def no_sleep(_delay):
        return None

    return await test_setpoint_live(fake, input_fn=input_fn, sleep_fn=no_sleep, print_fn=print_fn)


# --- CLI ---------------------------------------------------------------------------

def _build_parser():
    parser = argparse.ArgumentParser(
        prog="python -m villa_kuppis.pump_probe",
        description="Supervised first probe for MELCloud Home air-to-air units. "
                    "The demo is fully offline; live commands need --live and an "
                    "interactive terminal.",
    )
    parser.add_argument("--live", action="store_true",
                        help="talk to the real MELCloud Home cloud (read, test-setpoint only)")
    parser.add_argument("command", nargs="?", default="demo",
                        choices=("demo", "read", "test-setpoint"),
                        help="default: demo (offline)")
    return parser


async def _live_run(command, client_factory, credentials, input_fn, sleep_fn, print_fn):
    client, closer = client_factory(credentials[0], credentials[1])
    outcome = EXIT_ERROR
    lifecycle_error = None
    try:
        async with client:
            if command == "read":
                outcome = await read_live(client, print_fn=print_fn)
            else:
                outcome = await test_setpoint_live(
                    client, input_fn=input_fn, sleep_fn=sleep_fn, print_fn=print_fn
                )
    except BaseException as err:
        lifecycle_error = err
    finally:
        try:
            await closer()
        except Exception:
            # A session-close failure must never mask the probe result.
            pass
    if lifecycle_error is not None:
        if outcome == EXIT_RESTORE_UNCONFIRMED:
            # The unconfirmed-restore banner is the critical state; a teardown
            # failure after it must not overwrite it.
            return outcome
        if isinstance(lifecycle_error, (KeyboardInterrupt, asyncio.CancelledError)):
            print_fn("Interrupted.")
        elif isinstance(lifecycle_error, EOFError):
            print_fn("Input ended; the live session was aborted.")
        else:
            print_fn(_generic_message(lifecycle_error))
        return EXIT_ERROR
    return outcome


def main(argv=None, *, client_factory=None, getpass_fn=None, input_fn=None,
         sleep_fn=None, isatty_fn=None, print_fn=None):
    """CLI entry point. Returns an exit code (never raises on expected errors)."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    print_fn = print_fn or print
    command = args.command

    if command == "demo" and args.live:
        parser.error("demo is fully offline; --live is not allowed with it")
    if command in ("read", "test-setpoint") and not args.live:
        print_fn(f"'{command}' talks to the real MELCloud Home cloud; re-run with --live.")
        return EXIT_ERROR

    if command == "demo":
        return asyncio.run(_demo(print_fn=print_fn))

    # Live path: interactive terminal first, then getpass, then the client.
    try:
        isatty_fn = isatty_fn or (lambda: bool(sys.stdin.isatty()))
        if not isatty_fn():
            print_fn("Live commands need an interactive terminal (stdin must be a TTY).")
            return EXIT_ERROR
        getpass_fn = getpass_fn or getpass.getpass
        credentials = _prompt_credentials(getpass_fn, print_fn)
        if credentials is None:
            print_fn("Could not read credentials securely; aborting without signing in.")
            return EXIT_ERROR
        client_factory = client_factory or _default_client_factory

        async def _async_sleep(delay):
            await asyncio.sleep(delay)

        return asyncio.run(_live_run(
            command, client_factory, credentials,
            input_fn or input, sleep_fn or _async_sleep, print_fn,
        ))
    except _mel_exceptions() as exc:
        print_fn(_generic_message(exc))
        return EXIT_ERROR
    except KeyboardInterrupt:
        print_fn("Interrupted.")
        return EXIT_ERROR
    except EOFError:
        print_fn("Input ended; the live session was aborted.")
        return EXIT_ERROR
    except Exception:
        # Sanitized last resort: an unexpected internal error (e.g. a model
        # validation failure inside the library) must never escape as a raw
        # traceback that could carry payload data, account IDs, or tokens.
        print_fn("An unexpected internal error occurred; details are hidden for safety.")
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
