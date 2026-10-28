"""Home Assistant device gateway for one site (card H1.2).

Villa Kuppis talks to each site's Home Assistant over its WebSocket API:
an authenticated snapshot (``get_states``), a ``state_changed``
subscription, and ``call_service`` for commands. Devices keep stable
Villa Kuppis IDs; the mapping to Home Assistant ``entity_id`` strings is
stored per site and never guessed. A bound entity that disappears (removed
or renamed in Home Assistant) is a mapping error, and commands to it fail
instead of reaching another device.

Every command ends in one of four states:

- ``pending``: accepted and in flight;
- ``confirmed``: Home Assistant reported the requested value;
- ``failed``: refused before sending, or Home Assistant returned an error,
  so the device was not changed by this command;
- ``unknown``: sent, but neither an error nor the new value came back in
  time (connection lost, slow device). A later state report that matches
  still confirms it.

After a disconnect the gateway reconnects with backoff, takes a fresh
snapshot and resubscribes; a periodic snapshot also covers lost events.
Older reports than the one already held (reordered events) are ignored.

The gateway runs in its own thread and event loop like ``PumpGateway``;
``run()`` is also usable directly from an asyncio test.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import math
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

import aiohttp

KINDS = ("climate", "sensor", "number")
DOMAINS = {
    "climate": ("climate",),
    "sensor": ("sensor",),
    "number": ("input_number", "number"),
}
ACTIONS = {
    "climate": ("power", "setpoint", "mode", "fan"),
    "number": ("value",),
    "sensor": (),
}
UNAVAILABLE = ("unavailable", "unknown")

# Home Assistant ClimateEntityFeature bits.
FEATURE_TARGET_TEMPERATURE = 1
FEATURE_FAN_MODE = 8
FEATURE_TURN_OFF = 128
FEATURE_TURN_ON = 256

DEFAULT_COMMAND_TIMEOUT = 15.0     # wait for Home Assistant's call result
DEFAULT_CONFIRM_TIMEOUT = 90.0     # wait for the new value after that
DEFAULT_RESYNC_INTERVAL = 300.0    # periodic snapshot while connected
DEFAULT_ENTITY_STALE_AFTER = 1800.0
DEFAULT_SITE_STALE_AFTER = 180.0
RETRY_BASE = 5.0
RETRY_MAX = 300.0
COMMAND_HISTORY = 200
REGISTRY_DEBOUNCE = 2.0            # gather a burst of registry changes
# Registry changes that can add, move or hide a device (devices.py).
REGISTRY_EVENTS = ("area_registry_updated", "device_registry_updated",
                   "entity_registry_updated", "floor_registry_updated")
# Commands to any entity (devices.py) are tracked under this prefix.
ENTITY_PREFIX = "entity:"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class HAError(Exception):
    """Home Assistant answered a request with an error."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class HAAuthError(Exception):
    """The access token was refused."""


class HAConnectionClosed(ConnectionError):
    """The WebSocket closed before the request was answered."""


# --- connection ------------------------------------------------------------------

class HAConnection:
    """One authenticated Home Assistant WebSocket connection.

    A reader task resolves request futures by message id and hands
    subscription events to their callbacks. When the socket closes, every
    open request fails with HAConnectionClosed and ``wait_closed`` returns.
    """

    def __init__(self, session: aiohttp.ClientSession, ws, ha_version: str):
        self._session = session
        self._ws = ws
        self.ha_version = ha_version
        self._ids = itertools.count(1)
        self._futures: dict[int, asyncio.Future] = {}
        self._handlers: dict[int, Callable[[dict], None]] = {}
        self._closed = asyncio.Event()
        self.on_message: Callable[[], None] | None = None
        self._reader = asyncio.create_task(self._read())

    @classmethod
    async def open(cls, url: str, token: str, *, timeout: float = 15.0) -> "HAConnection":
        session = aiohttp.ClientSession()
        ws = None
        try:
            ws = await session.ws_connect(
                url, heartbeat=30.0, max_msg_size=0,
                timeout=aiohttp.ClientWSTimeout(ws_close=timeout),
            )
            hello = await asyncio.wait_for(ws.receive_json(), timeout)
            if hello.get("type") != "auth_required":
                raise HAError("protocol", f"unexpected first message {hello.get('type')!r}")
            await ws.send_json({"type": "auth", "access_token": token})
            reply = await asyncio.wait_for(ws.receive_json(), timeout)
            if reply.get("type") == "auth_invalid":
                raise HAAuthError(reply.get("message") or "access token refused")
            if reply.get("type") != "auth_ok":
                raise HAError("protocol", f"unexpected auth reply {reply.get('type')!r}")
            return cls(session, ws, str(reply.get("ha_version") or hello.get("ha_version") or ""))
        except BaseException:
            if ws is not None:
                await ws.close()
            await session.close()
            raise

    async def _read(self) -> None:
        try:
            async for msg in self._ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                data = json.loads(msg.data)
                # Home Assistant may coalesce several messages into a list.
                for item in data if isinstance(data, list) else [data]:
                    try:
                        self._dispatch(item)
                    except Exception:
                        pass   # one malformed message must not end the reader
        except (aiohttp.ClientError, ValueError, asyncio.TimeoutError):
            pass
        finally:
            for fut in self._futures.values():
                if not fut.done():
                    fut.set_exception(HAConnectionClosed("connection closed"))
            self._futures.clear()
            self._closed.set()

    def _dispatch(self, item: dict) -> None:
        if self.on_message is not None:
            self.on_message()
        kind = item.get("type")
        msg_id = item.get("id")
        if kind == "event":
            handler = self._handlers.get(msg_id)
            if handler is not None:
                handler(item.get("event") or {})
            return
        fut = self._futures.pop(msg_id, None)
        if fut is None or fut.done():
            return
        if kind == "result":
            if item.get("success"):
                fut.set_result(item.get("result"))
            else:
                err = item.get("error") or {}
                fut.set_exception(HAError(str(err.get("code") or "error"),
                                          str(err.get("message") or "")))
        elif kind == "pong":
            fut.set_result(None)

    async def request(self, payload: dict, timeout: float | None = None) -> Any:
        if self._closed.is_set():
            raise HAConnectionClosed("connection closed")
        msg_id = next(self._ids)
        fut = asyncio.get_running_loop().create_future()
        self._futures[msg_id] = fut
        try:
            await self._ws.send_json({**payload, "id": msg_id})
        except (aiohttp.ClientError, ConnectionError) as err:
            self._futures.pop(msg_id, None)
            raise HAConnectionClosed(str(err)) from err
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._futures.pop(msg_id, None)

    async def get_states(self, timeout: float | None = None) -> list[dict]:
        return await self.request({"type": "get_states"}, timeout)

    async def subscribe_state_changed(self, handler: Callable[[dict], None],
                                      timeout: float | None = None) -> None:
        await self.subscribe_events("state_changed", handler, timeout)

    async def subscribe_events(self, event_type: str, handler: Callable[[dict], None],
                               timeout: float | None = None) -> None:
        msg_id = next(self._ids)
        self._handlers[msg_id] = handler
        fut = asyncio.get_running_loop().create_future()
        self._futures[msg_id] = fut
        try:
            await self._ws.send_json({"id": msg_id, "type": "subscribe_events",
                                      "event_type": event_type})
            await asyncio.wait_for(fut, timeout)
        except BaseException:
            self._handlers.pop(msg_id, None)
            self._futures.pop(msg_id, None)
            raise

    async def get_services(self, timeout: float | None = None) -> dict:
        return await self.request({"type": "get_services"}, timeout)

    async def call_service(self, domain: str, service: str, entity_id: str | None,
                           data: dict, timeout: float | None = None, *,
                           target: dict | None = None) -> Any:
        payload = {"type": "call_service", "domain": domain, "service": service,
                   "service_data": data}
        if entity_id is not None:
            payload["target"] = {"entity_id": entity_id}
        elif target is not None:
            payload["target"] = dict(target)
        return await self.request(payload, timeout)

    async def wait_closed(self) -> None:
        await self._closed.wait()

    async def close(self) -> None:
        try:
            await self._ws.close()
        finally:
            await self._session.close()
            await asyncio.gather(self._reader, return_exceptions=True)


# --- bindings --------------------------------------------------------------------

@dataclass(frozen=True)
class Binding:
    site_id: str
    device_id: str
    entity_id: str
    kind: str
    label: str = ""
    room: str | None = None


class BindingError(ValueError):
    pass


_BINDING_SCHEMA = """
CREATE TABLE IF NOT EXISTS device_binding (
    site_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('climate','sensor','number')),
    label TEXT NOT NULL DEFAULT '',
    bound_at_utc TEXT NOT NULL,
    room TEXT,
    PRIMARY KEY (site_id, device_id),
    UNIQUE (site_id, entity_id)
);
"""


def check_binding(entity_id: str, kind: str) -> None:
    if kind not in KINDS:
        raise BindingError(f"kind must be one of {', '.join(KINDS)}")
    domain, dot, rest = entity_id.partition(".")
    if not dot or not rest or domain not in DOMAINS[kind]:
        raise BindingError(f"{entity_id!r} is not a {kind} entity")


class BindingStore:
    """Stable Villa Kuppis device IDs mapped to Home Assistant entities."""

    def __init__(self, path):
        self._path = Path(path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.executescript(_BINDING_SCHEMA)
        columns = [r[1] for r in self._conn.execute("PRAGMA table_info(device_binding)")]
        if "room" not in columns:
            self._conn.execute("ALTER TABLE device_binding ADD COLUMN room TEXT")
        self._conn.commit()

    def bind(self, site_id: str, device_id: str, entity_id: str, kind: str,
             label: str = "", room: str | None = None, *, now=utc_now) -> Binding:
        check_binding(entity_id, kind)
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO device_binding (site_id, device_id, entity_id, kind, "
                    "label, bound_at_utc, room) VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (site_id, device_id) DO UPDATE SET "
                    "entity_id = excluded.entity_id, kind = excluded.kind, "
                    "label = excluded.label, bound_at_utc = excluded.bound_at_utc, "
                    "room = excluded.room",
                    (site_id, device_id, entity_id, kind, label, now().isoformat(), room),
                )
                self._conn.commit()
            except sqlite3.IntegrityError as err:
                self._conn.rollback()
                raise BindingError(f"{entity_id} is already bound to another device") from err
        return Binding(site_id, device_id, entity_id, kind, label, room)

    def unbind(self, site_id: str, device_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM device_binding WHERE site_id = ? AND device_id = ?",
                (site_id, device_id))
            self._conn.commit()
            return cur.rowcount > 0

    def bindings(self, site_id: str) -> list[Binding]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT site_id, device_id, entity_id, kind, label, room FROM device_binding "
                "WHERE site_id = ? ORDER BY device_id", (site_id,)).fetchall()
        return [Binding(*row) for row in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# --- state normalization ---------------------------------------------------------

def _parse_ts(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        ts = datetime.fromisoformat(value)
    except ValueError:
        return None
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)


def _seen_at(ha_state: dict) -> datetime | None:
    """Latest time Home Assistant wrote this state, changed or not."""
    stamps = [_parse_ts(ha_state.get(k)) for k in ("last_reported", "last_updated")]
    stamps = [s for s in stamps if s is not None]
    return max(stamps) if stamps else None


def _number(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def capabilities(kind: str, attrs: dict) -> dict:
    """Only what the entity actually reports."""
    if kind == "climate":
        features = attrs.get("supported_features")
        features = features if isinstance(features, int) else 0
        return {
            "setpoint": bool(features & FEATURE_TARGET_TEMPERATURE),
            "power": bool(features & FEATURE_TURN_OFF) and bool(features & FEATURE_TURN_ON),
            "fan": bool(features & FEATURE_FAN_MODE),
            "min": _number(attrs.get("min_temp")),
            "max": _number(attrs.get("max_temp")),
            "step": _number(attrs.get("target_temp_step")),
            "modes": [m for m in attrs.get("hvac_modes") or [] if isinstance(m, str)],
            "fan_modes": [m for m in attrs.get("fan_modes") or [] if isinstance(m, str)],
        }
    if kind == "number":
        return {"min": _number(attrs.get("min")), "max": _number(attrs.get("max")),
                "step": _number(attrs.get("step"))}
    return {"unit": attrs.get("unit_of_measurement"),
            "device_class": attrs.get("device_class")}


def reading(binding: Binding, ha_state: dict) -> dict:
    """Normalize one Home Assistant state for a bound device."""
    attrs = ha_state.get("attributes") or {}
    raw = ha_state.get("state")
    available = raw not in UNAVAILABLE and raw is not None
    seen = _seen_at(ha_state)
    out = {
        "device_id": binding.device_id,
        "site_id": binding.site_id,
        "entity_id": binding.entity_id,
        "kind": binding.kind,
        "label": binding.label or attrs.get("friendly_name") or binding.device_id,
        "available": available,
        "source_ts": seen.isoformat() if seen is not None else None,
        "caps": capabilities(binding.kind, attrs),
    }
    if binding.kind == "climate":
        out.update({
            "power": (raw != "off") if available else None,
            "mode": raw if available else None,
            "setpoint": _number(attrs.get("temperature")) if available else None,
            "room_temp": _number(attrs.get("current_temperature")) if available else None,
            "fan": attrs.get("fan_mode") if available else None,
        })
    else:
        out["value"] = _number(raw) if available else None
        if binding.kind == "sensor":
            out["attributes"] = dict(attrs)
    return out


def validate(state: dict, action: str, value):
    """Check one command against the latest reading.

    Returns (ok, normalized_value, detail).
    """
    kind = state["kind"]
    if action not in ACTIONS.get(kind, ()):
        if not ACTIONS.get(kind):
            return False, None, "the device is read-only"
        return False, None, f"action must be one of {', '.join(ACTIONS[kind])}"
    if not state.get("available"):
        return False, None, "the device is unavailable in Home Assistant"
    caps = state["caps"]
    if action == "power":
        if not isinstance(value, bool):
            return False, None, "value must be true or false"
        if not caps["power"]:
            return False, None, "the device cannot be turned on and off"
        return True, value, ""
    if action == "mode":
        if not isinstance(value, str) or value not in caps["modes"]:
            return False, None, "the mode is not supported by this device"
        return True, value, ""
    if action == "fan":
        if not caps["fan"] or not isinstance(value, str) or value not in caps["fan_modes"]:
            return False, None, "the fan speed is not supported by this device"
        return True, value, ""
    # setpoint or number value
    if action == "setpoint" and not caps["setpoint"]:
        return False, None, "the device has no setpoint"
    number = _number(value)
    if number is None or isinstance(value, bool):
        return False, None, "value must be a number"
    lo, hi, step = caps["min"], caps["max"], caps["step"]
    if lo is None or hi is None:
        return False, None, "the device has not reported its range"
    if not lo <= number <= hi:
        return False, None, f"value must be between {lo:g} and {hi:g}"
    if step:
        steps = (number - lo) / step
        if abs(steps - round(steps)) > 1e-6:
            return False, None, f"value must be in steps of {step:g}"
    return True, number, ""


def matches(state: dict | None, action: str, value) -> bool:
    if state is None or not state.get("available"):
        return False
    if action == "power":
        return state["power"] == value
    if action == "mode":
        return state["mode"] == value
    if action == "fan":
        return state["fan"] == value
    current = state["setpoint"] if action == "setpoint" else state["value"]
    return current is not None and abs(current - value) < 1e-6


def service_call(binding: Binding, action: str, value) -> tuple[str, str, dict]:
    if action == "power":
        return "climate", "turn_on" if value else "turn_off", {}
    if action == "setpoint":
        return "climate", "set_temperature", {"temperature": value}
    if action == "mode":
        return "climate", "set_hvac_mode", {"hvac_mode": value}
    if action == "fan":
        return "climate", "set_fan_mode", {"fan_mode": value}
    return binding.entity_id.partition(".")[0], "set_value", {"value": value}


# --- commands --------------------------------------------------------------------

@dataclass
class Command:
    request_id: str
    site_id: str
    device_id: str
    action: str
    value: Any
    actor: str
    state: str = "pending"
    detail: str = ""
    created_at: str = ""
    finished_at: str | None = None
    _done: asyncio.Event | None = field(default=None, repr=False, compare=False)
    # A service command: (domain, service, entity target or None) and a
    # read-back check on the bound device's reading.
    _service: tuple | None = field(default=None, repr=False, compare=False)
    _confirm: Callable[[dict], bool] | None = field(default=None, repr=False, compare=False)
    # An entity command (submit_entity): the entity, checked on its raw state.
    _entity: str | None = field(default=None, repr=False, compare=False)

    def confirmed_by(self, state: dict | None) -> bool:
        if self._confirm is not None:
            return state is not None and bool(self._confirm(state))
        if self._entity is not None:
            return False
        return matches(state, self.action, self.value)

    def public(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}


# --- gateway ---------------------------------------------------------------------

class HomeAssistantGateway:
    """One site's Home Assistant: bound devices, readings and commands.

    ``on_command_done`` receives each finished command (for the event log);
    it runs under the gateway lock and must not call back into the gateway.
    """

    def __init__(self, site_id: str, url: str, token_provider: Callable[[], str],
                 bindings: BindingStore, *, now=utc_now,
                 connect: Callable[[str, str], Awaitable[HAConnection]] | None = None,
                 command_timeout: float = DEFAULT_COMMAND_TIMEOUT,
                 confirm_timeout: float = DEFAULT_CONFIRM_TIMEOUT,
                 resync_interval: float = DEFAULT_RESYNC_INTERVAL,
                 entity_stale_after: float = DEFAULT_ENTITY_STALE_AFTER,
                 site_stale_after: float = DEFAULT_SITE_STALE_AFTER,
                 retry_base: float = RETRY_BASE, retry_max: float = RETRY_MAX,
                 on_command_done: Callable[[dict], None] | None = None):
        self.site_id = site_id
        self._url = url
        self._token_provider = token_provider
        self._bindings_store = bindings
        self._now = now
        self._connect = connect or (lambda u, t: HAConnection.open(u, t, timeout=command_timeout))
        self._command_timeout = command_timeout
        self._confirm_timeout = confirm_timeout
        self._resync_interval = resync_interval
        self._entity_stale_after = entity_stale_after
        self._site_stale_after = site_stale_after
        self._retry_base = retry_base
        self._retry_max = retry_max
        self._on_command_done = on_command_done

        self._lock = threading.Lock()
        self._bindings: dict[str, Binding] = {}
        self._by_entity: dict[str, Binding] = {}
        self._states: dict[str, dict] = {}
        self._raw: dict[str, dict] = {}          # entity_id -> latest HA state
        self._missing: set[str] = set()
        self._commands: dict[str, Command] = {}
        self._active: dict[str, Command] = {}    # device_id -> pending command
        self._latest: dict[str, Command] = {}    # device_id -> last command
        self._conn: HAConnection | None = None
        self._connected = False
        self._state = "stopped"
        self._last_contact: datetime | None = None
        self._last_error: str | None = None
        self._ha_version = ""
        self._registry: dict | None = None
        self._registry_error: str | None = None
        self._registry_entities: frozenset = frozenset()
        self._registry_dirty: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._stop_requested = False
        self._thread: threading.Thread | None = None
        self.reload_bindings()

    # -- bindings --

    def reload_bindings(self) -> None:
        bindings = {b.device_id: b for b in self._bindings_store.bindings(self.site_id)}
        with self._lock:
            self._bindings = bindings
            self._by_entity = {b.entity_id: b for b in bindings.values()}
            self._states = {k: v for k, v in self._states.items() if k in bindings}
            for entity_id, raw in self._raw.items():
                self._apply_locked(entity_id, raw, force=True)
            self._refresh_missing_locked()

    def _refresh_missing_locked(self) -> None:
        if not self._raw and not self._connected:
            self._missing = set()
            return
        self._missing = {d for d, b in self._bindings.items() if b.entity_id not in self._raw}
        for device_id in self._missing:
            self._states.pop(device_id, None)

    def discover(self) -> list[dict]:
        """Entities from the latest snapshot that could be bound (read-only)."""
        domains = {d: kind for kind, ds in DOMAINS.items() for d in ds}
        with self._lock:
            out = []
            for entity_id, raw in sorted(self._raw.items()):
                kind = domains.get(entity_id.partition(".")[0])
                if kind is None:
                    continue
                bound = self._by_entity.get(entity_id)
                out.append({
                    "entity_id": entity_id, "kind": kind,
                    "name": (raw.get("attributes") or {}).get("friendly_name") or "",
                    "device_id": bound.device_id if bound else None,
                })
            return out

    # -- readings --

    def _apply_locked(self, entity_id: str, raw: dict | None, *, force: bool = False) -> str | None:
        """Store one HA state; returns the bound device id when it changed."""
        if raw is None:
            self._raw.pop(entity_id, None)
            binding = self._by_entity.get(entity_id)
            if binding is not None:
                self._states.pop(binding.device_id, None)
                self._missing.add(binding.device_id)
            return None
        held = self._raw.get(entity_id)
        if not force and held is not None:
            new_ts, old_ts = _seen_at(raw), _seen_at(held)
            if new_ts is not None and old_ts is not None and new_ts < old_ts:
                return None   # an older report arriving late
        self._raw[entity_id] = raw
        self._apply_reported_locked(raw)
        binding = self._by_entity.get(entity_id)
        if binding is None:
            return None
        self._states[binding.device_id] = reading(binding, raw)
        self._missing.discard(binding.device_id)
        return binding.device_id

    def _apply_reported_locked(self, raw: dict) -> None:
        """Freshen entities listed in a ``reported`` attribute (entity -> time).

        Home Assistant does not send ``last_reported`` for an unchanged
        reading, so a sensor that holds its value looks stale; the cottage
        integration's status sensors publish those times instead.
        """
        reported = (raw.get("attributes") or {}).get("reported")
        if not isinstance(reported, dict):
            return
        for entity_id, value in reported.items():
            held = self._raw.get(entity_id) if isinstance(entity_id, str) else None
            seen = _parse_ts(value)
            if held is None or seen is None:
                continue
            old = _seen_at(held)
            if old is not None and seen <= old:
                continue
            held = {**held, "last_reported": seen.isoformat()}
            self._raw[entity_id] = held
            binding = self._by_entity.get(entity_id)
            if binding is not None:
                self._states[binding.device_id] = reading(binding, held)

    def _status_locked(self, device_id: str, now: datetime) -> str:
        if device_id in self._missing:
            return "missing"
        state = self._states.get(device_id)
        if state is None:
            return "unknown"
        if not self._connected:
            return "stale"
        if not state["available"]:
            return "unavailable"
        seen = _parse_ts(state["source_ts"])
        if seen is not None and (now - seen).total_seconds() > self._entity_stale_after:
            return "stale"
        return "ok"

    def raw_states(self) -> dict[str, dict]:
        """The latest Home Assistant state of every entity (entity_id -> state)."""
        with self._lock:
            return dict(self._raw)

    def bound_entities(self) -> set[str]:
        """Entity ids bound to Villa Kuppis devices at this site."""
        with self._lock:
            return set(self._by_entity)

    def registry(self) -> dict | None:
        """Areas, floors, devices and entities from Home Assistant's
        registries (None until loaded or when Home Assistant refuses them).
        Read-only for the caller."""
        with self._lock:
            return self._registry

    def attributes(self, entity_id: str) -> dict | None:
        """The latest attributes of any entity, bound or not (None when absent)."""
        with self._lock:
            raw = self._raw.get(entity_id)
            return dict(raw.get("attributes") or {}) if raw is not None else None

    def snapshot(self) -> dict[str, dict]:
        """Every bound device: its latest reading, status and pending command."""
        now = self._now()
        with self._lock:
            out = {}
            for device_id, binding in self._bindings.items():
                state = dict(self._states.get(device_id) or {
                    "device_id": device_id, "site_id": self.site_id,
                    "entity_id": binding.entity_id, "kind": binding.kind,
                    "label": binding.label or device_id, "available": False,
                })
                state["status"] = self._status_locked(device_id, now)
                active = self._active.get(device_id)
                state["pending"] = active.public() if active else None
                out[device_id] = state
            return out

    def health(self) -> dict:
        now = self._now()
        with self._lock:
            state = self._state
            last = self._last_contact
            if state == "ok" and (last is None or
                                  (now - last).total_seconds() > self._site_stale_after):
                state = "stale"
            return {
                "site_id": self.site_id,
                "state": state,
                "last_contact": last.isoformat() if last else None,
                "error": self._last_error,
                "missing": sorted(self._missing),
                "ha_version": self._ha_version,
                "registry_error": self._registry_error,
            }

    # -- commands --

    def command(self, request_id: str) -> dict | None:
        with self._lock:
            cmd = self._commands.get(request_id)
            return cmd.public() if cmd else None

    def submit(self, device_id: str, action: str, value, actor: str) -> dict:
        """Validate and queue a device command; thread-safe. Returns the command.

        An identical command already pending is returned instead of a new
        one; a different command while one is pending fails at once, so two
        commands for one device never race.
        """
        cmd = Command(uuid.uuid4().hex, self.site_id, device_id, action, value,
                      actor, created_at=self._now().isoformat())

        def check(state):
            ok, normalized, detail = validate(state, action, value)
            if ok:
                cmd.value = normalized
            return ok, detail
        return self._submit(cmd, check)

    def submit_service(self, device_id: str, domain: str, service: str, data: dict,
                       actor: str, confirm: Callable[[dict], bool], *,
                       target_entity: bool = False) -> dict:
        """Queue a Home Assistant service call tracked through a bound device.

        ``confirm`` receives the device's reading (with ``attributes`` for a
        sensor) and returns True once the call has taken effect. The call
        targets the device's entity only with ``target_entity``.
        """
        cmd = Command(uuid.uuid4().hex, self.site_id, device_id, f"{domain}.{service}",
                      dict(data), actor, created_at=self._now().isoformat())
        cmd._confirm = confirm
        cmd._service = (domain, service, target_entity)
        return self._submit(cmd, lambda state: (True, ""))

    def _submit(self, cmd: Command, check) -> dict:
        device_id = cmd.device_id
        with self._lock:
            binding = self._bindings.get(device_id)
            active = self._active.get(device_id)
            if binding is None:
                return self._finish_locked(cmd, "failed", "the device is not bound at this site")
            if device_id in self._missing:
                return self._finish_locked(
                    cmd, "failed", f"{binding.entity_id} is missing in Home Assistant; check the binding")
            if active is not None:
                if active.action == cmd.action and active.value == cmd.value:
                    return active.public()
                return self._finish_locked(cmd, "failed", "another command for this device is pending")
            loop = self._loop
            if not self._connected or loop is None or loop.is_closed():
                return self._finish_locked(cmd, "failed", "Home Assistant is not connected")
            state = self._states.get(device_id)
            ok, detail = check(state) if state else (False, "the device has not reported its state")
            if not ok:
                return self._finish_locked(cmd, "failed", detail)
            if cmd.confirmed_by(state):
                return self._finish_locked(cmd, "confirmed", "already at the requested value")
            out = self._activate_locked(cmd)
        return self._launch(cmd, binding, loop, out)

    def submit_entity(self, entity_id: str, domain: str, service: str, data: dict,
                      actor: str, confirm: Callable[[dict], bool] | None = None) -> dict:
        """Queue a service call on any entity, bound or not (devices.py).

        ``confirm`` receives the entity's Home Assistant state and returns
        True once the call has taken effect; without it the command is
        confirmed when Home Assistant accepts the call. One command per
        entity at a time, as with bound devices.
        """
        key = ENTITY_PREFIX + entity_id
        cmd = Command(uuid.uuid4().hex, self.site_id, key, f"{domain}.{service}",
                      dict(data), actor, created_at=self._now().isoformat())
        cmd._service = (domain, service, True)
        cmd._confirm = confirm
        cmd._entity = entity_id
        with self._lock:
            active = self._active.get(key)
            if active is not None:
                if active.action == cmd.action and active.value == cmd.value:
                    return active.public()
                return self._finish_locked(cmd, "failed", "another command for this device is pending")
            loop = self._loop
            if not self._connected or loop is None or loop.is_closed():
                return self._finish_locked(cmd, "failed", "Home Assistant is not connected")
            raw = self._raw.get(entity_id)
            if raw is None:
                return self._finish_locked(cmd, "failed", f"{entity_id} is missing in Home Assistant")
            if raw.get("state") in UNAVAILABLE:
                return self._finish_locked(cmd, "failed", "the device is unavailable in Home Assistant")
            if confirm is not None and confirm(raw):
                return self._finish_locked(cmd, "confirmed", "already at the requested value")
            out = self._activate_locked(cmd)
        return self._launch(cmd, Binding(self.site_id, key, entity_id, "entity"), loop, out)

    def _activate_locked(self, cmd: Command) -> dict:
        self._active[cmd.device_id] = cmd
        self._commands[cmd.request_id] = cmd
        self._latest[cmd.device_id] = cmd
        return cmd.public()

    def _launch(self, cmd: Command, binding: Binding, loop, out: dict) -> dict:
        try:
            loop.call_soon_threadsafe(lambda: loop.create_task(self._execute(cmd, binding)))
        except RuntimeError:
            with self._lock:
                return self._finish_locked(cmd, "failed", "Home Assistant is not connected")
        return out

    def statistics(self, entity_id: str, start: datetime) -> list[tuple[int, float]]:
        """Hourly means of one entity from Home Assistant's long-term
        statistics since ``start``: (epoch seconds, mean); blocking."""
        result = self._run(lambda conn: conn.request({
            "type": "recorder/statistics_during_period", "start_time": start.isoformat(),
            "statistic_ids": [entity_id], "period": "hour", "types": ["mean"]},
            self._command_timeout))
        out = []
        for row in (result or {}).get(entity_id) or []:
            mean, begin = _number(row.get("mean")), row.get("start")
            if isinstance(begin, (int, float)) and not isinstance(begin, bool):
                ts = int(begin / 1000)        # milliseconds since Home Assistant 2023.3
            else:
                parsed = _parse_ts(begin)
                ts = int(parsed.timestamp()) if parsed is not None else None
            if mean is not None and ts is not None:
                out.append((ts, mean))
        return out

    def services(self, domain: str) -> list[str]:
        """Names of one domain's services in Home Assistant; blocking.

        Raises HAConnectionClosed while disconnected. Not for the gateway's
        own event loop thread.
        """
        result = self._run(lambda conn: conn.get_services(timeout=self._command_timeout))
        return sorted((result or {}).get(domain) or {})

    def call(self, domain: str, service: str, data: dict,
             entity_id: str | None = None, *, target: dict | None = None) -> None:
        """Call a service outside the device commands (a notification, a
        reload, all lights of an area); blocking.

        Raises HAError when Home Assistant refuses it and HAConnectionClosed
        or TimeoutError when there is no answer.
        """
        self._run(lambda conn: conn.call_service(domain, service, entity_id, data,
                                                 timeout=self._command_timeout,
                                                 target=target))

    def _run(self, make):
        with self._lock:
            conn, loop = self._conn, self._loop
        if conn is None or loop is None or loop.is_closed():
            raise HAConnectionClosed("Home Assistant is not connected")
        future = asyncio.run_coroutine_threadsafe(make(conn), loop)
        try:
            return future.result(self._command_timeout + 5)
        except TimeoutError:
            future.cancel()
            raise

    def _finish_locked(self, cmd: Command, result: str, detail: str) -> dict:
        cmd.state = result
        cmd.detail = detail
        cmd.finished_at = self._now().isoformat()
        if self._active.get(cmd.device_id) is cmd:
            del self._active[cmd.device_id]
        self._commands[cmd.request_id] = cmd
        self._latest.setdefault(cmd.device_id, cmd)
        while len(self._commands) > COMMAND_HISTORY:
            self._commands.pop(next(iter(self._commands)))
        if cmd._done is not None:
            cmd._done.set()
        out = cmd.public()
        if self._on_command_done is not None:
            try:
                self._on_command_done(out)
            except Exception:
                pass
        return out

    async def wait(self, request_id: str, timeout: float | None = None) -> dict | None:
        """Wait until a command leaves pending (for asyncio callers)."""
        with self._lock:
            cmd = self._commands.get(request_id)
            if cmd is None or cmd.state != "pending":
                return cmd.public() if cmd else None
            if cmd._done is None:
                cmd._done = asyncio.Event()
            done = cmd._done
        await asyncio.wait_for(done.wait(), timeout)
        return self.command(request_id)

    async def _execute(self, cmd: Command, binding: Binding) -> None:
        if cmd._service is not None:
            domain, service, target_entity = cmd._service
            data, entity_id = cmd.value, binding.entity_id if target_entity else None
        else:
            domain, service, data = service_call(binding, cmd.action, cmd.value)
            entity_id = binding.entity_id
        with self._lock:
            conn = self._conn
            if cmd._done is None:
                cmd._done = asyncio.Event()
            done = cmd._done
        try:
            if conn is None:
                raise HAConnectionClosed("not connected")
            await conn.call_service(domain, service, entity_id, data,
                                    timeout=self._command_timeout)
        except HAError as err:
            with self._lock:
                self._finish_locked(cmd, "failed", f"Home Assistant refused the command: {err.message or err.code}")
            return
        except (HAConnectionClosed, asyncio.TimeoutError, aiohttp.ClientError):
            with self._lock:
                self._finish_locked(cmd, "unknown",
                                    "no answer from Home Assistant; the change may have been applied")
            return
        with self._lock:
            if cmd.state != "pending":
                return
            if cmd._entity is not None and cmd._confirm is None:
                self._finish_locked(cmd, "confirmed", "")
                return
            if cmd.confirmed_by(self._reading_locked(cmd)):
                self._finish_locked(cmd, "confirmed", "")
                return
        try:
            await asyncio.wait_for(done.wait(), self._confirm_timeout)
        except asyncio.TimeoutError:
            with self._lock:
                if cmd.state == "pending":
                    self._finish_locked(
                        cmd, "unknown",
                        f"Home Assistant accepted the command but did not report the new value within {self._confirm_timeout:g} s")

    def _confirm_locked(self, device_id: str) -> None:
        """Confirm the device's pending or last unknown command if it now matches."""
        cmd = self._active.get(device_id) or self._latest.get(device_id)
        if cmd is None or cmd.state not in ("pending", "unknown"):
            return
        if cmd.confirmed_by(self._reading_locked(cmd)):
            was_unknown = cmd.state == "unknown"
            if was_unknown:
                cmd._done = None
            self._finish_locked(cmd, "confirmed", "confirmed by a later report" if was_unknown else "")

    def _reading_locked(self, cmd: Command) -> dict | None:
        if cmd._entity is not None:
            return self._raw.get(cmd._entity)
        return self._states.get(cmd.device_id)

    # -- connection loop --

    def _on_state_changed(self, event: dict) -> None:
        data = event.get("data") or {}
        entity_id = data.get("entity_id")
        if not isinstance(entity_id, str):
            return
        new = data.get("new_state")
        with self._lock:
            device_id = self._apply_locked(entity_id, new)
            if device_id is not None:
                self._confirm_locked(device_id)
            self._confirm_locked(ENTITY_PREFIX + entity_id)
            dirty = self._registry_dirty
            added = (new is not None and self._registry is not None
                     and entity_id not in self._registry_entities)
        if added and dirty is not None:
            dirty.set()   # a new entity: its area and device come from the registry

    def _touch(self) -> None:
        with self._lock:
            self._last_contact = self._now()

    async def _resync(self, conn: HAConnection) -> None:
        states = await conn.get_states(timeout=self._command_timeout)
        with self._lock:
            fresh = {s["entity_id"]: s for s in states
                     if isinstance(s, dict) and isinstance(s.get("entity_id"), str)}
            for entity_id in [e for e in self._raw if e not in fresh]:
                self._apply_locked(entity_id, None)
            for entity_id, raw in fresh.items():
                device_id = self._apply_locked(entity_id, raw)
                if device_id is not None:
                    self._confirm_locked(device_id)
            for key in [k for k in self._active if k.startswith(ENTITY_PREFIX)]:
                self._confirm_locked(key)
            self._refresh_missing_locked()

    async def _load_registry(self, conn: HAConnection) -> None:
        timeout = self._command_timeout
        try:
            areas = await conn.request({"type": "config/area_registry/list"}, timeout)
            devices = await conn.request({"type": "config/device_registry/list"}, timeout)
            entities = await conn.request({"type": "config/entity_registry/list"}, timeout)
        except (HAError, asyncio.TimeoutError) as err:
            with self._lock:
                self._registry_error = f"{type(err).__name__}: {err}".rstrip(": ")
            return
        try:
            floors = await conn.request({"type": "config/floor_registry/list"}, timeout)
        except (HAError, asyncio.TimeoutError):
            floors = []    # Home Assistant before floors (2024.4)

        def rows(value):
            return [r for r in value if isinstance(r, dict)] if isinstance(value, list) else []
        registry = {"areas": rows(areas), "floors": rows(floors), "devices": rows(devices),
                    "entities": rows(entities), "loaded_at": self._now().isoformat()}
        with self._lock:
            self._registry = registry
            self._registry_entities = frozenset(e.get("entity_id") for e in registry["entities"])
            self._registry_error = None

    async def _registry_loop(self, conn: HAConnection) -> None:
        """Keep the registries current: at connect, after a registry event
        or a new entity, and at every resync interval."""
        dirty = asyncio.Event()
        with self._lock:
            self._registry_dirty = dirty
        try:
            for event_type in REGISTRY_EVENTS:
                try:
                    await conn.subscribe_events(event_type, lambda _e: dirty.set(),
                                                timeout=self._command_timeout)
                except (HAError, asyncio.TimeoutError):
                    pass   # not allowed for this user: the interval still applies
            while True:
                dirty.clear()
                await self._load_registry(conn)
                try:
                    await asyncio.wait_for(dirty.wait(), self._resync_interval)
                    await asyncio.sleep(REGISTRY_DEBOUNCE)
                except asyncio.TimeoutError:
                    pass
        except HAConnectionClosed:
            return
        finally:
            with self._lock:
                if self._registry_dirty is dirty:
                    self._registry_dirty = None

    def retry_delay(self, failures: int, auth_failed: bool = False) -> float:
        if auth_failed:
            return self._retry_max
        return min(self._retry_base * 2 ** max(failures - 1, 0), self._retry_max)

    def _set_state(self, state: str, error: str | None = None) -> None:
        with self._lock:
            self._state = state
            self._last_error = error

    async def _session(self) -> None:
        conn = await self._connect(self._url, self._token_provider())
        conn.on_message = self._touch
        try:
            self._touch()
            with self._lock:
                self._ha_version = conn.ha_version
            # Subscribe first so nothing between the snapshot and the
            # subscription is lost; older events are dropped by timestamp.
            await conn.subscribe_state_changed(self._on_state_changed,
                                               timeout=self._command_timeout)
            with self._lock:
                self._conn = conn
                self._connected = True
            await self._resync(conn)
            self._set_state("ok")
            closed = asyncio.ensure_future(conn.wait_closed())
            stop = asyncio.ensure_future(self._stop.wait())
            registry = asyncio.ensure_future(self._registry_loop(conn))
            try:
                while True:
                    done, _ = await asyncio.wait({closed, stop}, timeout=self._resync_interval,
                                                 return_when=asyncio.FIRST_COMPLETED)
                    if done:
                        return
                    await self._resync(conn)
            finally:
                closed.cancel()
                stop.cancel()
                registry.cancel()
                await asyncio.gather(registry, return_exceptions=True)
        finally:
            with self._lock:
                self._conn = None
                self._connected = False
            await conn.close()

    async def run(self) -> None:
        """Connect, keep the snapshot current and reconnect until stopped."""
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        if self._stop_requested:
            self._stop.set()
        failures = 0
        while not self._stop.is_set():
            self._set_state("connecting", self.health()["error"])
            auth_failed = False
            try:
                await self._session()
                if self._stop.is_set():
                    break
                failures = 1
                self._set_state("disconnected", "the connection to Home Assistant closed")
            except asyncio.CancelledError:
                raise
            except HAAuthError as err:
                failures += 1
                auth_failed = True
                self._set_state("auth_failed", f"access token refused: {err}")
            except Exception as err:
                failures += 1
                self._set_state("disconnected", f"{type(err).__name__}: {err}".rstrip(": "))
            try:
                await asyncio.wait_for(self._stop.wait(), self.retry_delay(failures, auth_failed))
            except asyncio.TimeoutError:
                pass
        self._set_state("stopped", None)

    def request_stop(self) -> None:
        self._stop_requested = True
        loop, stop = self._loop, self._stop
        if loop is not None and stop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(stop.set)

    # -- thread runner --

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=lambda: asyncio.run(self.run()),
                                        name=f"ha-gateway-{self.site_id}", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 30.0) -> None:
        self.request_stop()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None
