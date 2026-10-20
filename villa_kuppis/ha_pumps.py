"""Home pumps through Home Assistant's climate entities (card H1.5).

``HAPumpClient`` gives ``PumpGateway`` the two calls it uses from the
``aiomelcloudhome`` client, ``get_context()`` and ``control_ata_unit()``,
backed by a ``HomeAssistantGateway``. Everything above it (the pump
service, cooling automation, optimizer, history and UI) stays unchanged,
and switching back to the direct MELCloud Home client is a configuration
change (``pumps`` in the runtime configuration).

Each pump is bound in the home site's ``sites.db`` with its existing
MELCloud unit id as the device id, so history, settings and plans carry
over. Home Assistant reports one temperature range for all modes; it is
used for every mode. The HVAC and fan names below follow Home Assistant's
climate conventions; the MELCloud Home integration's exact fan names are
checked in H1.3.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

HVAC_TO_MODE = {"cool": "Cool", "heat": "Heat", "dry": "Dry", "fan_only": "Fan",
                "heat_cool": "Automatic", "auto": "Automatic"}
FAN_WORDS = {"auto": "Auto", "one": "One", "two": "Two", "three": "Three",
             "four": "Four", "five": "Five", "1": "One", "2": "Two", "3": "Three",
             "4": "Four", "5": "Five"}
CONNECTED = ("ok", "stale")


class PumpCommandError(RuntimeError):
    pass


def fan_name(ha_fan) -> str | None:
    return FAN_WORDS.get(str(ha_fan).lower()) if ha_fan is not None else None


def _named(value):
    return SimpleNamespace(value=value) if value is not None else None


def unit_from_state(state: dict):
    """An object shaped like an aiomelcloudhome air-to-air unit."""
    caps = state.get("caps") or {}
    available = state.get("available") and state.get("status") in ("ok", "stale")
    modes = {HVAC_TO_MODE.get(m) for m in caps.get("modes", [])}
    fans = [fan_name(f) for f in caps.get("fan_modes", [])]
    lo, hi, step = caps.get("min"), caps.get("max"), caps.get("step")
    capabilities = SimpleNamespace(
        min_temp_heat=lo, max_temp_heat=hi, min_temp_cool=lo, max_temp_cool=hi,
        min_temp_auto=lo, max_temp_auto=hi,
        has_half_degree_increments=(None if step is None else abs(step - 0.5) < 1e-6),
        number_of_fan_speeds=sum(1 for f in fans if f not in (None, "Auto")) or None,
        has_automatic_fan_speed="Auto" in fans,
        has_cool_operation_mode="Cool" in modes,
        has_heat_operation_mode="Heat" in modes,
        has_dry_operation_mode="Dry" in modes,
        has_fan_operation_mode="Fan" in modes,
        has_auto_operation_mode="Automatic" in modes,
    )
    mode = HVAC_TO_MODE.get(state.get("mode")) if available else None
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

    def __init__(self, gateway, *, write_timeout: float = 20.0, poll: float = 0.2):
        self._gateway = gateway
        self._write_timeout = write_timeout
        self._poll = poll

    def _climates(self) -> dict:
        return {d: s for d, s in self._gateway.snapshot().items() if s.get("kind") == "climate"}

    async def get_context(self):
        health = self._gateway.health()
        if health["state"] not in CONNECTED:
            raise ConnectionError(f"Home Assistant {health['state']}: {health.get('error') or ''}".rstrip(": "))
        units = [unit_from_state(s) for s in self._climates().values()
                 if s.get("status") != "missing"]
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
        action, value = self._translate(unit_id, kwargs)
        cmd = self._gateway.submit(unit_id, action, value, "Villa Kuppis")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._write_timeout
        while cmd["state"] == "pending" and loop.time() < deadline:
            await asyncio.sleep(self._poll)
            cmd = self._gateway.command(cmd["request_id"]) or cmd
        if cmd["state"] != "confirmed":
            raise PumpCommandError(cmd.get("detail") or cmd["state"])


def client_factory(gateway):
    """A PumpGateway client factory: starts the HA gateway thread with the
    pump loop and stops it at teardown."""
    def factory():
        gateway.start()

        async def teardown():
            await asyncio.get_running_loop().run_in_executor(None, gateway.stop)
        return HAPumpClient(gateway), teardown
    return factory
