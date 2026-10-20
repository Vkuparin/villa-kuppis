"""One status sensor per room: the target in force and why.

State: the effective room target (°C), or unknown in manual mode.
Attributes carry what Villa Kuppis reads back after ``set_target``
(revision, requested target, validity) and the controller's mode.
"""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from . import DOMAIN, SIGNAL_UPDATE, CottageController, Room


async def async_setup_platform(hass: HomeAssistant, config, async_add_entities,
                               discovery_info=None) -> None:
    if discovery_info is None:
        return
    controller: CottageController = hass.data[DOMAIN]
    async_add_entities(RoomTargetSensor(room) for room in controller.rooms.values())


class RoomTargetSensor(SensorEntity):
    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(self, room: Room):
        self._room = room
        self._attr_name = f"Villa Kuppis {room.name}"
        self._attr_unique_id = f"{DOMAIN}_{room.room_id}_target"
        self.entity_id = f"sensor.villa_kuppis_{room.room_id}_target"

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_UPDATE, self._update))

    @callback
    def _update(self, room_id: str) -> None:
        if room_id == self._room.room_id:
            self.async_write_ha_state()

    @property
    def native_value(self):
        decision = self._room.decision
        return decision.effective_target if decision else None

    @property
    def extra_state_attributes(self) -> dict:
        room, decision, target = self._room, self._room.decision, self._room.target
        memory = room.memory
        return {
            "mode": decision.mode if decision else None,
            "reason": decision.reason if decision else None,
            "drive": decision.drive if decision else None,
            "room_temperature": room.room_temp,
            "requested_target": target.value if target else None,
            "revision": target.revision if target else None,
            "valid_until": target.valid_until.isoformat() if target else None,
            "manual_until": memory.manual_until.isoformat() if memory.manual_until else None,
            "manual_by": memory.manual_by,
        }
