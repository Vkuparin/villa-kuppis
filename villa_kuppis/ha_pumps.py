"""Home pumps through Home Assistant's climate entities (card H1.5).

``HAPumpClient`` gives ``PumpGateway`` the two calls it uses from the
``aiomelcloudhome`` client, ``get_context()`` and ``control_ata_unit()``,
backed by a ``HomeAssistantGateway``. Everything above it (the pump
service, cooling automation, optimizer, history and UI) stays unchanged,
and switching back to the direct MELCloud Home client is a configuration
change (``pumps`` in the runtime configuration).

Each pump is bound in the home site's ``sites.db`` with its existing
MELCloud unit id as the device id, so history, settings and plans carry
over. Home Assistant reports one temperature range for all modes (7-35 °C
from the MELCloud Home integration) and no temperature step, while MELCloud
reports a range per mode (16-31 °C for cooling) and half-degree steps. The
pump store keeps what the direct client last reported, so those values win
inside Home Assistant's range; without them the one range is used for every
mode. The MELCloud Home integration names its fan speeds ``speed_1`` to
``speed_5`` (H1.3, 25 October 2026).

A climate entity that is off reports the HVAC mode ``off`` and hides the
unit's mode, while MELCloud keeps reporting it. The cooling rules act only
on units in Cool, including turning back on a unit they switched off, so
the client remembers each unit's last mode while it is off (seeded from the
pump store after a restart). For the same reason a mode change on an off
unit is not sent: ``climate.set_hvac_mode`` would switch the unit on,
while MELCloud changes the mode and leaves the unit off. The remembered
mode is accepted without a write; any other mode is refused.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from types import SimpleNamespace

from .ha_gateway import HAError

log = logging.getLogger(__name__)

HVAC_TO_MODE = {"cool": "Cool", "heat": "Heat", "dry": "Dry", "fan_only": "Fan",
                "heat_cool": "Automatic", "auto": "Automatic"}
FAN_WORDS = {"auto": "Auto", "one": "One", "two": "Two", "three": "Three",
             "four": "Four", "five": "Five", "1": "One", "2": "Two", "3": "Three",
             "4": "Four", "5": "Five", "speed_1": "One", "speed_2": "Two",
             "speed_3": "Three", "speed_4": "Four", "speed_5": "Five"}
MODE_RANGES = (("min_temp_heat", "min_heat"), ("max_temp_heat", "max_heat"),
               ("min_temp_cool", "min_cool"), ("max_temp_cool", "max_cool"),
               ("min_temp_auto", "min_auto"), ("max_temp_auto", "max_auto"))
CONNECTED = ("ok", "stale")
RELOAD_AFTER = 600.0     # every pump unavailable this long
RELOAD_EVERY = 3600.0    # at most one reload an hour


class PumpCommandError(RuntimeError):
    pass


def fan_name(ha_fan) -> str | None:
    return FAN_WORDS.get(str(ha_fan).lower()) if ha_fan is not None else None


def _named(value):
    return SimpleNamespace(value=value) if value is not None else None


def _within(known, lo, hi, use_min: bool):
    if known is None:
        return lo if use_min else hi
    if lo is not None and known < lo:
        return lo
    if hi is not None and known > hi:
        return hi
    return known


def unit_from_state(state: dict, last_mode: str | None = None, known_caps: dict | None = None):
    """An object shaped like an aiomelcloudhome air-to-air unit.

    ``last_mode`` stands in for the hidden mode of a unit that is off;
    ``known_caps`` (the pump store's caps) supplies the per-mode ranges and
    the step that Home Assistant does not report."""
    caps = state.get("caps") or {}
    available = state.get("available") and state.get("status") in ("ok", "stale")
    modes = {HVAC_TO_MODE.get(m) for m in caps.get("modes", [])}
    fans = [fan_name(f) for f in caps.get("fan_modes", [])]
    lo, hi, step = caps.get("min"), caps.get("max"), caps.get("step")
    known = known_caps or {}
    ranges = {attr: _within(known.get(key), lo, hi, attr.startswith("min"))
              for attr, key in MODE_RANGES}
    capabilities = SimpleNamespace(
        **ranges,
        has_half_degree_increments=(known.get("half_step") if step is None
                                    else abs(step - 0.5) < 1e-6),
        number_of_fan_speeds=(sum(1 for f in fans if f not in (None, "Auto"))
                              or known.get("fan_speeds")),
        has_automatic_fan_speed="Auto" in fans,
        has_cool_operation_mode="Cool" in modes,
        has_heat_operation_mode="Heat" in modes,
        has_dry_operation_mode="Dry" in modes,
        has_fan_operation_mode="Fan" in modes,
        has_auto_operation_mode="Automatic" in modes,
    )
    mode = None
    if available:
        mode = last_mode if state.get("mode") == "off" else HVAC_TO_MODE.get(state.get("mode"))
    return SimpleNamespace(
        id=state["device_id"],
        name=state.get("label") or state["device_id"],
        power=state.get("power") if available else None,
        operation_mode=_named(mode),
        set_temperature=state.get("setpoint") if available else None,
        room_temperature=state.get("room_temp") if available else None,
        set_fan_speed=_named(fan_name(state.get("fan")) if available else None),
        is_in_error=not available,
        capabilities=capabilities,
    )


class HAPumpClient:
    """The part of the MELCloud Home client API that PumpGateway calls."""

    def __init__(self, gateway, *, write_timeout: float = 20.0, poll: float = 0.2,
                 connect_wait: float = 15.0, stored: list | None = None):
        self._gateway = gateway
        self._last_modes = {u["id"]: u["mode"] for u in stored or [] if u.get("mode")}
        self._known_caps = {u["id"]: u["caps"] for u in stored or [] if u.get("caps")}
        self._write_timeout = write_timeout
        self._poll = poll
        self._connect_wait = connect_wait

    def _climates(self) -> dict:
        return {d: s for d, s in self._gateway.snapshot().items() if s.get("kind") == "climate"}

    async def get_context(self):
        # Right after start the first read would otherwise fail and wait a
        # whole (backed-off) poll interval.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._connect_wait
        health = self._gateway.health()
        while health["state"] in ("stopped", "connecting") and loop.time() < deadline:
            await asyncio.sleep(self._poll)
            health = self._gateway.health()
        if health["state"] not in CONNECTED:
            raise ConnectionError(f"Home Assistant {health['state']}: {health.get('error') or ''}".rstrip(": "))
        units = []
        for state in self._climates().values():
            if state.get("status") == "missing":
                continue
            unit = unit_from_state(state, self._last_modes.get(state["device_id"]),
                                   self._known_caps.get(state["device_id"]))
            if unit.operation_mode is not None:
                self._last_modes[state["device_id"]] = unit.operation_mode.value
            units.append(unit)
        return SimpleNamespace(buildings=[SimpleNamespace(air_to_air_units=units)])

    def _translate(self, unit_id: str, kwargs: dict) -> tuple[str, object]:
        if len(kwargs) != 1:
            raise PumpCommandError("one change at a time")
        key, value = next(iter(kwargs.items()))
        if key == "power":
            return "power", bool(value)
        if key == "set_temperature":
            return "setpoint", value
        state = self._climates().get(unit_id) or {}
        caps = state.get("caps") or {}
        if key == "operation_mode":
            wanted = getattr(value, "value", value)
            for hvac in caps.get("modes", []):
                if HVAC_TO_MODE.get(hvac) == wanted:
                    return "mode", hvac
            raise PumpCommandError(f"mode {wanted} is not offered by Home Assistant")
        if key == "set_fan_speed":
            wanted = getattr(value, "value", value)
            for fan in caps.get("fan_modes", []):
                if fan_name(fan) == wanted:
                    return "fan", fan
            raise PumpCommandError(f"fan speed {wanted} is not offered by Home Assistant")
        raise PumpCommandError(f"unsupported change {key}")

    async def control_ata_unit(self, unit_id: str, **kwargs) -> None:
        """Send one change; raises unless Home Assistant accepted it.

        PumpGateway confirms the change with its own read-backs, so an
        accepted-but-unconfirmed (``unknown``) command raises like an HTTP
        error after a write that may have succeeded.
        """
        if "operation_mode" in kwargs:
            state = self._climates().get(unit_id) or {}
            if state.get("mode") == "off":
                wanted = getattr(kwargs["operation_mode"], "value", kwargs["operation_mode"])
                if wanted == self._last_modes.get(unit_id):
                    return
                raise PumpCommandError("the unit is off; Home Assistant would switch "
                                       "it on to change the mode")
        action, value = self._translate(unit_id, kwargs)
        cmd = self._gateway.submit(unit_id, action, value, "Villa Kuppis")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._write_timeout
        while cmd["state"] == "pending" and loop.time() < deadline:
            await asyncio.sleep(self._poll)
            cmd = self._gateway.command(cmd["request_id"]) or cmd
        if cmd["state"] != "confirmed":
            raise PumpCommandError(cmd.get("detail") or cmd["state"])


def client_factory(gateway, stored=None):
    """A PumpGateway client factory: starts the HA gateway thread with the
    pump loop and stops it at teardown. ``stored`` returns the pump store's
    unit states (last mode and capabilities)."""
    def factory():
        gateway.start()

        async def teardown():
            await asyncio.get_running_loop().run_in_executor(None, gateway.stop)
        return HAPumpClient(gateway, stored=stored() if stored else None), teardown
    return factory


class SessionReloader:
    """Reloads the pumps' Home Assistant integration when every pump drops out.

    A MELCloud Home session can expire (HTTP 401, "Session expired") and leave
    every pump unavailable until someone signs in again; reloading the config
    entry signs in again with the e-mail and password it stores. The reload
    waits until every pump has been unavailable for RELOAD_AFTER seconds while
    Home Assistant itself answers, and runs at most every RELOAD_EVERY
    seconds. Home Assistant allows it only for an administrator user.
    """

    def __init__(self, gateway, *, now=lambda: datetime.now(timezone.utc)):
        self._gateway = gateway
        self._now = now
        self._since: datetime | None = None
        self._last: datetime | None = None
        self.tried = False   # reloaded during the current outage

    def check(self, units: list[dict], enabled: bool) -> None:
        now = self._now()
        if not units or not all(u["power"] is None and u["mode"] is None for u in units):
            self._since, self.tried = None, False
            return
        if self._since is None:
            self._since = now
        if (not enabled or self._gateway.health()["state"] != "ok"
                or (now - self._since).total_seconds() < RELOAD_AFTER
                or (self._last is not None
                    and (now - self._last).total_seconds() < RELOAD_EVERY)):
            return
        entity = (self._gateway.snapshot().get(units[0]["id"]) or {}).get("entity_id")
        if entity is None:
            return
        self._last, self.tried = now, True
        try:
            self._gateway.call("homeassistant", "reload_config_entry", {}, entity_id=entity)
            log.info("Every pump was unavailable; reloaded their Home Assistant integration")
        except HAError as error:
            if error.code == "unauthorized":
                log.warning("Reloading the pumps' integration needs an administrator "
                            "user in Home Assistant")
            else:
                log.warning("Home Assistant refused to reload the pumps' integration: %s",
                            error.message or error.code)
        except Exception as error:
            log.warning("Reloading the pumps' integration failed: %s", type(error).__name__)
