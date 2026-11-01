"""The cottage's own alarm: phone notices from the cottage Home Assistant
when Villa Kuppis has gone silent.

Villa Kuppis (on the home server) sends every alert while it runs. It also
calls ``villa_kuppis_cottage.heartbeat`` every few minutes. When no
heartbeat has come for ``silent`` (the home server, its Home Assistant or
the link between the sites is down), this alarm says so once, and a room
below ``cold_below`` gets a notice from here too. The alarm waits for the
first heartbeat, so an older Villa Kuppis without heartbeats never sets it
off. Pure logic, no Home Assistant imports: the tests load it by path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass(frozen=True)
class AlarmSettings:
    cold_below: float = 3.0
    silent: timedelta = timedelta(minutes=60)
    clear_margin: float = 1.0   # a cold room is over this much above the limit


def _deg(value: float) -> str:
    return f"{value:.1f}".removesuffix(".0").replace(".", ",") + " °C"


@dataclass
class Alarm:
    settings: AlarmSettings = field(default_factory=AlarmSettings)
    heard: datetime | None = None          # the last heartbeat
    silent_sent: bool = False
    cold: set[str] = field(default_factory=set)   # rooms with a cold notice out

    def heartbeat(self, now: datetime) -> None:
        self.heard = now

    def silent(self, now: datetime) -> bool:
        return self.heard is not None and now - self.heard > self.settings.silent

    def check(self, now: datetime, rooms: dict[str, float | None],
              safe_setpoint: float) -> list[tuple[str, str]]:
        """Notices due now, as (title, message); ``rooms`` maps a room's
        name to its temperature (None: no reading)."""
        out = []
        silent = self.silent(now)
        if silent and not self.silent_sent:
            minutes = int(self.settings.silent.total_seconds() // 60)
            out.append(("Mökki: Villa Kuppis ei vastaa",
                        f"Mökin Home Assistant ei ole kuullut Villa Kuppisista {minutes} "
                        f"minuuttiin. Patterit pitävät varalämmön {_deg(safe_setpoint)}. "
                        "Tämä ilmoitus tulee mökin Home Assistantista."))
            self.silent_sent = True
        elif not silent and self.silent_sent:
            out.append(("Mökki: Villa Kuppis vastaa taas",
                        "Yhteys Villa Kuppisiin toimii taas."))
            self.silent_sent = False
        limit = self.settings.cold_below
        for name, temp in rooms.items():
            if temp is None:
                continue
            label = name if name.casefold() == "mökki" else f"Mökki: {name}"
            if name not in self.cold and silent and temp < limit:
                out.append((f"{label} on kylmä",
                            f"Lämpötila on {_deg(temp)}, alle {_deg(limit)}. Villa Kuppis "
                            "ei vastaa, joten tämä ilmoitus tulee mökin Home Assistantista."))
                self.cold.add(name)
            elif name in self.cold and temp >= limit + self.settings.clear_margin:
                out.append((f"{label} on taas lämpimämpi",
                            f"Lämpötila on nyt {_deg(temp)}."))
                self.cold.discard(name)
        return out
