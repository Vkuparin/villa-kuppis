"""PC pump service: state, commands and the event log for the home pumps.

Card F2.3. The service keeps the cloud-reported state of every air-to-air
unit in SQLite with a timestamp and a fresh/stale flag per reading, and a
separate pending slot per unit until the device confirms a requested
change. The device-reported state and the requested change are shown
separately, per the UI design (docs/ui-fi.md).

Commands go through the same supervised pipeline as the F2.1 probe: one
single-attempt HTTP write, then read-backs. A change is confirmed only
when a fresh read-back reports it; anything else is failed with a safe
detail message. Every command records who (or which plan) asked, the
unit, the requested value and its result in the SQLite event log.

The async MELCloud Home client runs in a background thread with its own
event loop; the synchronous API (view, request, events) is safe to call
from HTTP server threads. Live mode reads MELCloud Home credentials from
the environment (VILLA_KUPPIS_MELCLOUD_USERNAME /
VILLA_KUPPIS_MELCLOUD_PASSWORD) for development; F3.2 will move them to
the Windows credential store. Demo mode runs the same code against the
in-memory FakeClient, fully offline.
"""

from __future__ import annotations

import asyncio
import json
import math
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiomelcloudhome.models.ata import ATAFanSpeed, ATAOperationMode

from .pump_probe import (
    _default_client_factory,
    _operation_message,
    collect_units,
    find_unit_by_id,
)

DB_FILENAME = "pumps.db"
DEFAULT_POLL_INTERVAL = 10.0
DEFAULT_STALE_AFTER = 60.0
DEFAULT_READBACK_POLLS = 3
DEFAULT_READBACK_DELAY = 5.0
DEFAULT_COMMAND_TIMEOUT = 30.0
ORPHAN_PENDING_MAX_AGE = 120.0
MAX_ACTOR_LENGTH = 80
DEFAULT_ACTOR = "web"

MODES = ("Cool", "Heat", "Dry", "Fan", "Automatic")
FAN_NAMES = ("One", "Two", "Three", "Four", "Five")
ACTIONS = ("power", "setpoint", "mode", "fan")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


# --- validation ------------------------------------------------------------------

def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _bounds_for_mode(caps, mode):
    """(lo, hi) for a mode from a stored caps dict; (None, None) if unknown.

    This is the dict-shape counterpart of pump_probe._bounds_for_mode, which
    operates on the library's capabilities object.
    """
    if caps is None or mode is None:
        return None, None
    if mode == "Heat":
        return caps.get("min_heat"), caps.get("max_heat")
    if mode in ("Cool", "Dry"):
        return caps.get("min_cool"), caps.get("max_cool")
    if mode == "Automatic":
        return caps.get("min_auto"), caps.get("max_auto")
    return None, None


def validate_request(state: dict, action: str, value):
    """Validate one command against the latest stored state of the unit.

    Returns (ok, normalized_value, error_code, detail). The state dict is
    the shape produced by unit_state() (caps from the unit's capabilities).
    """
    caps = state.get("caps")

    if action == "power":
        if not isinstance(value, bool):
            return False, None, "bad_power", "value must be true or false"
        return True, value, None, ""

    if action == "setpoint":
        if not _is_number(value) or not math.isfinite(value):
            return False, None, "not_number", "value must be a number"
        mode = state.get("mode")
        if mode is None:
            return False, None, "no_mode", "the unit has not reported an operation mode"
        if mode == "Fan":
            return False, None, "fan_mode", "a setpoint is not available in fan mode"
        lo, hi = _bounds_for_mode(caps, mode)
        if lo is None or hi is None or not (math.isfinite(lo) and math.isfinite(hi)):
            return False, None, "no_bounds", "the unit has not reported temperature bounds for this mode"
        if not lo <= value <= hi:
            return False, None, "out_of_bounds", f"value must be between {lo:g} and {hi:g}"
        if caps is not None:
            half = caps.get("half_step")
            if half is True and abs(value * 2.0 - round(value * 2.0)) > 1e-6:
                return False, None, "step", "the value is not on the unit's half-degree steps"
            if half is False and abs(value - round(value)) > 1e-6:
                return False, None, "step", "the value is not on the unit's one-degree steps"
        return True, float(value), None, ""

    if action == "mode":
        allowed = [
            m for m in MODES
            if not (caps is not None and caps.get("modes", {}).get(m) is False)
        ]
        if not isinstance(value, str) or value not in allowed:
            return False, None, "bad_mode", "the mode is not supported by this unit"
        return True, value, None, ""

    if action == "fan":
        if not isinstance(value, str):
            return False, None, "bad_fan", "value must be a fan speed name"
        if value == "Auto":
            if caps is not None and caps.get("auto_fan") is False:
                return False, None, "bad_fan", "this unit has no automatic fan speed"
            return True, value, None, ""
        if value in FAN_NAMES:
            index = FAN_NAMES.index(value) + 1
            speeds = caps.get("fan_speeds") if caps is not None else None
            if speeds is not None and index > speeds:
                return False, None, "bad_fan", "this unit has no fan speed that high"
            return True, value, None, ""
        return False, None, "bad_fan", "the fan speed is not supported by this unit"

    return False, None, "unknown_action", f"action must be one of {', '.join(ACTIONS)}"


def _control_kwargs(action: str, value) -> dict:
    if action == "power":
        return {"power": value}
    if action == "setpoint":
        return {"set_temperature": value}
    if action == "mode":
        return {"operation_mode": ATAOperationMode(value)}
    if action == "fan":
        return {"set_fan_speed": ATAFanSpeed(value)}
    raise ValueError(action)


def _matches(unit, action: str, value) -> bool:
    if action == "power":
        return unit.power is not None and unit.power == value
    if action == "setpoint":
        st = unit.set_temperature
        return st is not None and math.isfinite(st) and st == value
    if action == "mode":
        return unit.operation_mode is not None and unit.operation_mode.value == value
    if action == "fan":
        return unit.set_fan_speed is not None and unit.set_fan_speed.value == value
    return False


def unit_state(unit, now_iso: str) -> dict:
    """Normalize one library unit into the state row shape."""
    caps = None
    if unit.capabilities is not None:
        c = unit.capabilities
        caps = {
            "min_heat": c.min_temp_heat,
            "max_heat": c.max_temp_heat,
            "min_cool": c.min_temp_cool,
            "max_cool": c.max_temp_cool,
            "min_auto": c.min_temp_auto,
            "max_auto": c.max_temp_auto,
            "half_step": c.has_half_degree_increments,
            "fan_speeds": c.number_of_fan_speeds,
            "auto_fan": c.has_automatic_fan_speed,
            "modes": {
                "Cool": c.has_cool_operation_mode,
                "Heat": c.has_heat_operation_mode,
                "Dry": c.has_dry_operation_mode,
                "Fan": c.has_fan_operation_mode,
                "Automatic": c.has_auto_operation_mode,
            },
        }

    def finite(v):
        return v if (isinstance(v, float) and math.isfinite(v)) else None

    mode = unit.operation_mode.value if unit.operation_mode is not None else None
    fan = unit.set_fan_speed.value if unit.set_fan_speed is not None else None
    return {
        "id": unit.id,
        "name": unit.name or "(unnamed)",
        "power": unit.power,
        "mode": mode,
        "setpoint": finite(unit.set_temperature),
        "fan": fan,
        "room_temp": finite(unit.room_temperature),
        "in_error": unit.is_in_error,
        "caps": caps,
        "updated_at": now_iso,
    }


# --- store -----------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS unit_state (
    unit_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    power INTEGER,
    mode TEXT,
    setpoint REAL,
    fan TEXT,
    room_temp REAL,
    in_error INTEGER,
    caps_json TEXT,
    updated_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc TEXT NOT NULL,
    unit_id TEXT NOT NULL,
    unit_name TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('power','setpoint','fan','mode')),
    requested TEXT NOT NULL,
    result TEXT NOT NULL CHECK (result IN ('pending','confirmed','failed')),
    detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_event_id_desc ON event (id DESC);
"""


class PumpStore:
    """SQLite state and event log. All methods are thread-safe (one lock)."""

    def __init__(self, path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def upsert_units(self, states: list[dict]) -> None:
        with self._lock:
            for s in states:
                self._conn.execute(
                    """INSERT INTO unit_state (unit_id, name, power, mode, setpoint,
                                               fan, room_temp, in_error, caps_json,
                                               updated_at_utc)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(unit_id) DO UPDATE SET
                         name=excluded.name,
                         power=excluded.power,
                         mode=excluded.mode,
                         setpoint=excluded.setpoint,
                         fan=excluded.fan,
                         room_temp=excluded.room_temp,
                         in_error=excluded.in_error,
                         caps_json=excluded.caps_json,
                         updated_at_utc=excluded.updated_at_utc""",
                    (
                        s["id"], s["name"], s["power"], s["mode"], s["setpoint"],
                        s["fan"], s["room_temp"], s["in_error"],
                        json.dumps(s["caps"]) if s["caps"] is not None else None,
                        s["updated_at"],
                    ),
                )
            self._conn.commit()

    def unit_state(self, unit_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM unit_state WHERE unit_id = ?", (unit_id,)
            ).fetchone()
        return self._row_to_state(row) if row is not None else None

    def unit_states(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM unit_state ORDER BY rowid"
            ).fetchall()
        return [self._row_to_state(r) for r in rows]

    @staticmethod
    def _row_to_state(row) -> dict:
        # SQLite stores booleans as 0/1; coerce them back on the way out.
        power = row["power"]
        in_error = row["in_error"]
        return {
            "id": row["unit_id"],
            "name": row["name"],
            "power": bool(power) if power is not None else None,
            "mode": row["mode"],
            "setpoint": row["setpoint"],
            "fan": row["fan"],
            "room_temp": row["room_temp"],
            "in_error": bool(in_error) if in_error is not None else None,
            "caps": json.loads(row["caps_json"]) if row["caps_json"] else None,
            "updated_at": row["updated_at_utc"],
        }

    def add_event(self, now_iso: str, unit_id: str, unit_name: str, actor: str,
                  action: str, requested) -> int:
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO event (ts_utc, unit_id, unit_name, actor, action,
                                      requested, result, detail)
                   VALUES (?, ?, ?, ?, ?, ?, 'pending', '')""",
                (now_iso, unit_id, unit_name, actor, action, json.dumps(requested)),
            )
            self._conn.commit()
            rowid = cur.lastrowid
            return int(rowid) if rowid is not None else 0

    def finish_event(self, event_id: int, result: str, detail: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE event SET result = ?, detail = ? WHERE id = ?",
                (result, detail, event_id),
            )
            self._conn.commit()

    def mark_stale_pending(self, now: datetime, max_age_seconds: float) -> int:
        """Mark pending events older than max_age as failed (orphaned)."""
        cutoff = (now - timedelta(seconds=max_age_seconds)).isoformat()
        with self._lock:
            cur = self._conn.execute(
                """UPDATE event SET result = 'failed',
                          detail = 'the service restarted before the change was confirmed'
                   WHERE result = 'pending' AND ts_utc < ?""",
                (cutoff,),
            )
            self._conn.commit()
            return cur.rowcount

    def events(self, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM event ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            {
                "id": r["id"],
                "ts": r["ts_utc"],
                "unit": r["unit_id"],
                "unit_name": r["unit_name"],
                "actor": r["actor"],
                "action": r["action"],
                "requested": json.loads(r["requested"]),
                "result": r["result"],
                "detail": r["detail"],
            }
            for r in rows
        ]

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# --- gateway (async client on its own loop) ---------------------------------------

class PumpGateway:
    """Runs the async MELCloud Home client in a background thread.

    A poll loop reads the context and upserts unit states; a worker consumes
    submitted commands. A pending slot is reserved at submit time so two
    concurrent commands for the same unit can never both enter the pipeline.
    """

    def __init__(self, client, store: PumpStore, *, now=utc_now,
                 poll_interval: float = DEFAULT_POLL_INTERVAL,
                 stale_after: float = DEFAULT_STALE_AFTER,
                 readback_polls: int = DEFAULT_READBACK_POLLS,
                 readback_delay: float = DEFAULT_READBACK_DELAY,
                 command_timeout: float = DEFAULT_COMMAND_TIMEOUT,
                 teardown=None):
        self._client = client
        self._store = store
        self._now = now
        self._poll_interval = poll_interval
        self._stale_after = stale_after
        self._readback_polls = readback_polls
        self._readback_delay = readback_delay
        self._command_timeout = command_timeout
        self._teardown = teardown

        self._lock = threading.Lock()
        self._last_ok: datetime | None = None
        self._last_error: str | None = None
        self._pending: dict[str, dict] = {}
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        # Safe to create outside the loop (Python 3.10+); bound to the
        # gateway loop on first use.
        self._queue: asyncio.Queue = asyncio.Queue()
        self._stop_event = asyncio.Event()
        self._started = False

    # -- lifecycle --

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._thread = threading.Thread(
            target=self._run_thread, name="pump-gateway", daemon=True
        )
        self._thread.start()

    def _run_thread(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._run())
        finally:
            self._loop.close()

    async def _run(self) -> None:
        poll_task = asyncio.create_task(self._poll_loop())
        worker_task = asyncio.create_task(self._worker())
        await self._stop_event.wait()
        poll_task.cancel()
        worker_task.cancel()
        await asyncio.gather(poll_task, worker_task, return_exceptions=True)
        if self._teardown is not None:
            try:
                await asyncio.wait_for(self._teardown(), timeout=self._command_timeout)
            except Exception:
                pass

    def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        loop = self._loop
        if loop is not None and loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._request_stop(), loop
            ).result(timeout=30.0)
        else:
            # The loop has not started (or has ended) yet; the flag is
            # checked when _run begins.
            self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=30.0)
            self._thread = None

    async def _request_stop(self) -> None:
        assert self._stop_event is not None
        self._stop_event.set()

    # -- status --

    @property
    def running(self) -> bool:
        return self._started

    def has_pending(self, unit_id: str) -> bool:
        with self._lock:
            return unit_id in self._pending

    def status(self) -> dict:
        now = self._now()
        with self._lock:
            last_ok = self._last_ok
            last_error = self._last_error
        if last_ok is None:
            state = "initializing" if last_error is None else "error"
        elif now - last_ok <= timedelta(seconds=self._stale_after):
            state = "ok"
        else:
            state = "stale"
        return {
            "state": state,
            "error": last_error,
            "last_ok": last_ok.isoformat() if last_ok is not None else None,
            "stale_after": self._stale_after,
        }

    def _pending_snapshot(self) -> dict[str, dict]:
        with self._lock:
            return {k: dict(v) for k, v in self._pending.items()}

    # -- polling --

    async def _poll_loop(self) -> None:
        while True:
            try:
                context = await asyncio.wait_for(
                    self._client.get_context(), timeout=self._command_timeout
                )
                now_iso = self._now().isoformat()
                states = [unit_state(u, now_iso) for u in collect_units(context)]
                self._store.upsert_units(states)
                with self._lock:
                    self._last_ok = self._now()
                    self._last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as err:
                with self._lock:
                    self._last_error = _operation_message("cloud read", err)
            await asyncio.sleep(self._poll_interval)

    # -- commands --

    def submit(self, event_id: int, unit_id: str, action: str, value, actor: str) -> bool:
        """Reserve the pending slot and queue the command. False if busy or stopped."""
        if not self._started or self._loop is None or self._loop.is_closed():
            return False
        with self._lock:
            if unit_id in self._pending:
                return False
            self._pending[unit_id] = {
                "action": action,
                "requested": value,
                "actor": actor,
                "started_at": self._now().isoformat(),
            }
        try:
            # The queue's getters wait on futures bound to the gateway loop,
            # so the put must be executed on that loop. A bare put_nowait
            # from this thread would set_result on the loop's futures from
            # the wrong thread and the wake-up can be silently lost until
            # the next timer fires. call_soon_threadsafe is the designed
            # cross-thread wakeup.
            self._loop.call_soon_threadsafe(
                self._queue.put_nowait, (event_id, unit_id, action, value, actor)
            )
        except RuntimeError:
            with self._lock:
                self._pending.pop(unit_id, None)
            return False
        return True

    async def _worker(self) -> None:
        while True:
            event_id, unit_id, action, value, actor = await self._queue.get()
            await self._execute(event_id, unit_id, action, value, actor)

    async def _read_unit(self, unit_id: str):
        context = await asyncio.wait_for(
            self._client.get_context(), timeout=self._command_timeout
        )
        return find_unit_by_id(context, unit_id)

    async def _execute(self, event_id: int, unit_id: str, action: str, value,
                       actor: str) -> None:
        result, detail = "failed", ""
        cancelled = False
        try:
            try:
                unit = await self._read_unit(unit_id)
            except Exception as err:
                detail = _operation_message("state read", err)
            else:
                if unit is None:
                    detail = "the unit was not found in MELCloud Home"
                else:
                    state = unit_state(unit, self._now().isoformat())
                    ok, value, code, msg = validate_request(state, action, value)
                    if not ok:
                        detail = msg
                    else:
                        result, detail = await self._write_and_verify(
                            unit_id, action, value
                        )
        except asyncio.CancelledError:
            # A command interrupted at shutdown stays pending; the next
            # start marks it as an orphaned failure.
            cancelled = True
            raise
        finally:
            with self._lock:
                self._pending.pop(unit_id, None)
            if not cancelled:
                try:
                    self._store.finish_event(event_id, result, detail)
                except sqlite3.Error:
                    pass

    async def _write_and_verify(self, unit_id: str, action: str, value) -> tuple[str, str]:
        kwargs = _control_kwargs(action, value)
        write_failed = False
        try:
            await asyncio.wait_for(
                self._client.control_ata_unit(unit_id, **kwargs),
                timeout=self._command_timeout,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            write_failed = True
        last_read_error = ""
        for attempt in range(1, self._readback_polls + 1):
            try:
                unit = await self._read_unit(unit_id)
                if unit is not None and _matches(unit, action, value):
                    # The read-back is a fresh cloud report: persist it so
                    # the view shows the confirmed value at once, not on
                    # the next poll.
                    try:
                        self._store.upsert_units(
                            [unit_state(unit, self._now().isoformat())]
                        )
                    except sqlite3.Error:
                        pass
                    detail = "the write response failed but the change was confirmed" if write_failed else ""
                    return "confirmed", detail
            except asyncio.CancelledError:
                raise
            except Exception as err:
                last_read_error = _operation_message(
                    f"read-back {attempt} of {self._readback_polls}", err
                )
            if attempt < self._readback_polls:
                await asyncio.sleep(self._readback_delay)
        if write_failed:
            return "failed", "the write response failed and the change was not confirmed"
        if last_read_error:
            return "failed", f"the change was not confirmed: {last_read_error}"
        return "failed", (
            f"the change was not confirmed after {self._readback_polls} read-backs"
        )


# --- service ---------------------------------------------------------------------

class PumpService:
    """Synchronous facade over the gateway and store, for the HTTP layer."""

    def __init__(self, data_dir, *, client, teardown=None, now=utc_now,
                 poll_interval: float = DEFAULT_POLL_INTERVAL,
                 stale_after: float = DEFAULT_STALE_AFTER,
                 readback_polls: int = DEFAULT_READBACK_POLLS,
                 readback_delay: float = DEFAULT_READBACK_DELAY,
                 command_timeout: float = DEFAULT_COMMAND_TIMEOUT):
        self._data_dir = Path(data_dir)
        self._now = now
        self._stale_after = stale_after
        self.store = PumpStore(self._data_dir / DB_FILENAME)
        self.gateway = PumpGateway(
            client,
            self.store,
            now=now,
            poll_interval=poll_interval,
            stale_after=stale_after,
            readback_polls=readback_polls,
            readback_delay=readback_delay,
            command_timeout=command_timeout,
            teardown=teardown,
        )
        self._closed = False

    # -- lifecycle --

    def start(self) -> None:
        # A command interrupted by the previous run's shutdown was never
        # confirmed; it cannot be confirmed now, so it is failed.
        self.store.mark_stale_pending(self._now(), ORPHAN_PENDING_MAX_AGE)
        self.gateway.start()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.gateway.stop()
        self.store.close()

    # -- reads --

    def view(self) -> dict:
        now = self._now()
        window = timedelta(seconds=self._stale_after)
        units = []
        for state in self.store.unit_states():
            fresh = False
            if state["updated_at"]:
                try:
                    updated = datetime.fromisoformat(state["updated_at"])
                    fresh = now - updated <= window
                except ValueError:
                    pass
            units.append({
                **{k: state[k] for k in (
                    "id", "name", "power", "mode", "setpoint", "fan",
                    "room_temp", "in_error", "caps",
                )},
                "updated_at": state["updated_at"],
                "fresh": fresh,
                "pending": None,
            })
        # Overlay pending slots on the matching units.
        pending_by_unit = self.gateway._pending_snapshot()
        for unit in units:
            if unit["id"] in pending_by_unit:
                unit["pending"] = pending_by_unit[unit["id"]]
        return {"service": self.gateway.status(), "units": units}

    def events(self, limit: int = 100) -> list[dict]:
        return self.store.events(limit)

    # -- writes --

    def request(self, unit_id: str, action: str, value, actor: str | None) -> tuple[int, dict]:
        if action not in ACTIONS:
            return 400, {"error": "unknown_action",
                         "detail": f"action must be one of {', '.join(ACTIONS)}"}
        if not self.gateway.running:
            return 503, {"error": "not_running",
                         "detail": "the pump service is not running"}
        state = self.store.unit_state(unit_id)
        if state is None:
            return 404, {"error": "unknown_unit",
                         "detail": "the unit is unknown to the service"}
        actor = (actor or "").strip()[:MAX_ACTOR_LENGTH] or DEFAULT_ACTOR
        if self.gateway.has_pending(unit_id):
            return 409, {"error": "pending",
                         "detail": "a previous change for this unit is still being confirmed"}
        ok, value, code, detail = validate_request(state, action, value)
        if not ok:
            return 400, {"error": code, "detail": detail}
        event_id = self.store.add_event(
            self._now().isoformat(), unit_id, state["name"], actor, action, value
        )
        if not self.gateway.submit(event_id, unit_id, action, value, actor):
            self.store.finish_event(event_id, "failed", "the service is shutting down")
            return 503, {"error": "not_running",
                         "detail": "the pump service is shutting down"}
        return 202, {"status": "pending", "unit": unit_id, "action": action,
                     "requested": value}


# --- client factories -------------------------------------------------------------

def make_live_service(data_dir, *, poll_interval=DEFAULT_POLL_INTERVAL,
                      stale_after=DEFAULT_STALE_AFTER,
                      readback_polls=DEFAULT_READBACK_POLLS,
                      readback_delay=DEFAULT_READBACK_DELAY,
                      command_timeout=DEFAULT_COMMAND_TIMEOUT) -> PumpService:
    """Live service from environment credentials (development mode).

    F3.2 will replace the environment credentials with the Windows
    credential store. The credential values are never printed or logged.
    """
    import os

    username = os.environ.get("VILLA_KUPPIS_MELCLOUD_USERNAME")
    password = os.environ.get("VILLA_KUPPIS_MELCLOUD_PASSWORD")
    if not username or not password:
        raise RuntimeError(
            "live mode requires the VILLA_KUPPIS_MELCLOUD_USERNAME and "
            "VILLA_KUPPIS_MELCLOUD_PASSWORD environment variables"
        )
    client, close_session = _default_client_factory(username, password)

    async def teardown():
        try:
            await client.__aexit__(None, None, None)
        except Exception:
            pass
        try:
            await close_session()
        except Exception:
            pass

    return PumpService(
        data_dir, client=client, teardown=teardown,
        poll_interval=poll_interval, stale_after=stale_after,
        readback_polls=readback_polls, readback_delay=readback_delay,
        command_timeout=command_timeout,
    )
