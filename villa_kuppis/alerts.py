"""Phone alerts through the home Home Assistant.

A notice goes to every phone signed in to the Home Assistant companion app
(the ``notify.mobile_app_*`` services of the home Home Assistant) when a
problem starts, and another when it is over:

* a cottage room colder than its limit (``alert_cold``, limit
  ``cold_alert_c``); it is over 1 °C above the limit, so a reading at the
  limit does not flap;
* the cottage Home Assistant unreachable for 30 minutes (``alert_offline``);
* a home pump unavailable or reporting a device error for 30 minutes
  (``alert_pump``, also per pump).

Each alert has its own switch setting, named above. Open problems live in
memory, so a problem that persists over a restart is reported again. A
notice that cannot be sent is retried at the next check.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

from . import settings as app_settings

log = logging.getLogger(__name__)

CHECK_INTERVAL = 60.0
OFFLINE_AFTER = 1800.0
PUMP_AFTER = 1800.0
COLD_CLEAR_MARGIN = 1.0
PHONE_PREFIX = "mobile_app_"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _deg(value: float) -> str:
    text = f"{value:.1f}".removesuffix(".0")
    return text.replace(".", ",") + " °C"


@dataclass(frozen=True)
class Problem:
    key: str
    setting: str               # the switch that turns this alert off
    site: str
    room: str | None
    start: tuple[str, str]     # title, message when it starts
    end: tuple[str, str]       # title, message when it is over
    after: float = 0.0         # seconds it must last before the notice


class HomeAssistantNotifier:
    """Sends a notice to every phone of one Home Assistant gateway."""

    def __init__(self, gateway):
        self._gateway = gateway
        self._phones: list[str] | None = None

    def send(self, title: str, message: str) -> bool:
        try:
            phones = [s for s in self._gateway.services("notify") if s.startswith(PHONE_PREFIX)]
        except Exception as error:
            log.warning("Alert not sent: Home Assistant unreachable (%s)", type(error).__name__)
            return False
        if phones != self._phones:
            self._phones = phones
            if phones:
                log.info("Alerts go to %d phone(s) in Home Assistant", len(phones))
            else:
                log.warning("Alert not sent: no phone has the Home Assistant app")
        sent = 0
        for phone in phones:
            try:
                self._gateway.call("notify", phone, {"title": title, "message": message})
                sent += 1
            except Exception as error:
                log.warning("Alert to %s failed: %s", phone, type(error).__name__)
        if sent:
            log.info("Alert sent: %s", title)
        return sent > 0


class AlertMonitor:
    """Checks the sites every minute and sends notices when problems start and end."""

    def __init__(self, service, notifier, settings, *, now=utc_now,
                 interval: float = CHECK_INTERVAL):
        self._service = service
        self._notifier = notifier
        self._settings = settings
        self._now = now
        self._interval = interval
        self._since: dict[str, datetime] = {}
        self._open: dict[str, Problem] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- checks --

    def problems(self, overrides) -> list[Problem]:
        out = []
        for unit in self._service.view()["units"]:
            unavailable = unit["power"] is None and unit["mode"] is None
            if not unavailable and not unit["in_error"]:
                continue
            name = unit["name"]
            text = ("Pumppu ei ole ollut käytettävissä 30 minuuttiin." if unavailable
                    else "Pumppu on ilmoittanut laitevirheestä 30 minuutin ajan.")
            out.append(Problem(f"pump:{unit['id']}", "alert_pump", "home", unit["id"],
                               (f"Koti: pumppu {name} ei toimi", text),
                               (f"Koti: pumppu {name} toimii taas",
                                "Pumppu on taas käytettävissä."), PUMP_AFTER))
        cottage = self._service.cottage
        if cottage is None:
            return out
        view = cottage.view()
        if view["site"]["connection"] != "ok":
            out.append(Problem("cottage:offline", "alert_offline", "cottage", None,
                               ("Mökki ei vastaa", "Villa Kuppis ei ole saanut yhteyttä "
                                "mökin Home Assistantiin 30 minuuttiin."),
                               ("Mökki vastaa taas", "Yhteys mökin Home Assistantiin toimii taas."),
                               OFFLINE_AFTER))
            # Old readings prove nothing: a cold room stays open, unreported.
            return out + [p for k, p in self._open.items() if k.startswith("cold:")]
        for room in view["rooms"]:
            key = f"cold:{room['id']}"
            value = (room.get("temperature") or {}).get("value")
            if value is None:
                if key in self._open:
                    out.append(self._open[key])
                continue
            limit = app_settings.resolve(overrides, "cottage", room["id"])["cold_alert_c"]["value"]
            if value < limit or (key in self._open and value < limit + COLD_CLEAR_MARGIN):
                out.append(Problem(key, "alert_cold", "cottage", room["id"],
                                   (f"Mökki: {room['name']} on kylmä",
                                    f"Lämpötila on {_deg(value)}, alle hälytysrajan {_deg(limit)}."),
                                   (f"Mökki: {room['name']} on taas lämpimämpi",
                                    f"Lämpötila on noussut yli {_deg(limit + COLD_CLEAR_MARGIN)}.")))
        return out

    def check(self) -> None:
        now = self._now()
        overrides = self._settings.overrides()

        def enabled(problem):
            return app_settings.resolve(overrides, problem.site, problem.room)[problem.setting]["value"]

        current = {p.key: p for p in self.problems(overrides)}
        for key in [k for k in self._since if k not in current]:
            del self._since[key]
        for key, problem in current.items():
            since = self._since.setdefault(key, now)
            if key in self._open or not enabled(problem):
                continue
            if (now - since).total_seconds() >= problem.after and self._notifier.send(*problem.start):
                self._open[key] = problem
        for key in [k for k in self._open if k not in current]:
            problem = self._open[key]
            if not enabled(problem) or self._notifier.send(*problem.end):
                del self._open[key]

    # -- thread --

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="alerts", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self.check()
            except Exception as error:
                log.warning("Alert check failed: %s", type(error).__name__)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=30.0)
            self._thread = None
