"""Stand-in cottage rooms: pure model, no Home Assistant imports.

Until the Mill Gen4 heaters and IKEA ALPSTUGA sensors are commissioned, the
``villa_kuppis_simulator`` integration gives the cottage Home Assistant
entities that look and behave like them, so ``villa_kuppis_cottage`` and
Villa Kuppis run against the live cottage-haos unchanged. Switching to the
real devices means changing the entity ids in the cottage package and
removing the simulator.

Each room is one thermal mass with a heat loss to the outdoors:

    C * dT/dt = P_heaters - UA * (T - T_out)

Each heater runs its own thermostat on its own sensor, like a Mill: it
measures ``sensor_bias`` above the room (the sensor sits on the panel) and
heats at full power below ``setpoint - THERMOSTAT_BAND`` until it reads
``setpoint + THERMOSTAT_BAND``. A heater keeps its thermostat running when
it drops off the network; only its reporting and commands stop.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

THERMOSTAT_BAND = 0.25      # °C each side of the heater setpoint
MAX_STEP_SECONDS = 60.0
MAX_CATCH_UP_SECONDS = 3 * 24 * 3600.0


@dataclass
class HeaterSim:
    key: str
    name: str
    power_w: float
    mode: str = "heat"                 # "heat" or "off"
    setpoint: float = 5.0
    min_temp: float = 5.0
    max_temp: float = 35.0
    step: float = 0.5
    sensor_bias: float = 0.5
    heating: bool = False
    available: bool = True


@dataclass
class RoomSim:
    key: str
    name: str
    temperature: float
    loss_w_per_k: float
    capacity_j_per_k: float
    heaters: list = field(default_factory=list)
    sensor_available: bool = True

    def heater_reading(self, heater: HeaterSim) -> float:
        return self.temperature + heater.sensor_bias

    def power_w(self) -> float:
        return sum(h.power_w for h in self.heaters if h.heating)


def thermostat(room: RoomSim, heater: HeaterSim) -> None:
    if heater.mode != "heat":
        heater.heating = False
        return
    reading = room.heater_reading(heater)
    if reading < heater.setpoint - THERMOSTAT_BAND:
        heater.heating = True
    elif reading >= heater.setpoint + THERMOSTAT_BAND:
        heater.heating = False


def advance(room: RoomSim, outdoor: float, seconds: float) -> None:
    """Run the room forward ``seconds`` (thermostats checked every step)."""
    seconds = min(max(seconds, 0.0), MAX_CATCH_UP_SECONDS)
    while seconds > 0:
        dt = min(seconds, MAX_STEP_SECONDS)
        for heater in room.heaters:
            thermostat(room, heater)
        flow = room.power_w() - room.loss_w_per_k * (room.temperature - outdoor)
        room.temperature += flow * dt / room.capacity_j_per_k
        seconds -= dt
    for heater in room.heaters:
        thermostat(room, heater)


def _saturation_hpa(t: float) -> float:
    return 6.112 * math.exp(17.62 * t / (243.12 + t))


def humidity(room_temp: float, outdoor: float, outdoor_rh: float = 85.0) -> float:
    """Relative humidity indoors when the air's moisture comes from outdoors."""
    vapour = _saturation_hpa(outdoor) * outdoor_rh / 100.0
    return min(max(100.0 * vapour / _saturation_hpa(room_temp), 10.0), 95.0)


def co2(rng: random.Random) -> float:
    return 420.0 + rng.uniform(0.0, 25.0)       # empty cottage: close to outdoor air


def pm25(rng: random.Random) -> float:
    return rng.uniform(1.0, 6.0)
