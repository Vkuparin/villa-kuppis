"""Phone alerts through the home Home Assistant.

A notice goes to every phone signed in to the Home Assistant companion app
(the ``notify.mobile_app_*`` services of the home Home Assistant) when a
problem starts, and another when it is over:

* a cottage room colder than its limit (``alert_cold``, limit
  ``cold_alert_c``); it is over 1 °C above the limit, so a reading at the
  limit does not flap;
* the cottage Home Assistant unreachable for 30 minutes (``alert_offline``);
* a cottage room without a fresh temperature reading for an hour, or a
  cottage heater unavailable for 30 minutes (``alert_device``); a heater
  switched off in Villa Kuppis is not reported (the cold alert still is);
* a cottage heater that should heat and does not, by its own sensor, after
  the integration sent its setpoint again and switched it off and on
  (``alert_device``; a Mill took setpoints over Matter and kept another);
* a home pump unavailable or reporting a device error for 30 minutes
  (``alert_pump``, also per pump).

A stale room reading is not used for the cold alert: an old value proves
nothing, so an open cold alert stays open and a new one waits for a fresh
reading.

Each alert has its own switch setting, named above. Open problems live in
memory, so a problem that persists over a restart is reported again. A
notice that cannot be sent is retried at the next check. The same check
runs the pumps' session reloader (``ha_pumps.SessionReloader``, setting
``pump_relogin``), so a pump alert comes only when the reload did not help.
It also reads the pumps' hourly energy (``ha_pumps.EnergyRecorder``) once
an hour.

A cottage room that has been over the humidity limit for a day gets a
notice (``alert_humidity``), and during a visit a room over 1200 ppm CO2
gets a reminder to air it (``alert_co2``, no notice when it is over). The
reminder waits over the night (22-08 Helsinki time) and comes in the
morning if the air is still stale.

Cottage visit notices are sent once per visit (``CottageSite.visit_notices``):
the rooms are warm (``alert_visit_warm``), a room will not be warm by the
arrival (``alert_visit_late``), and a visit without a departure time is still
on three days after the arrival (``alert_visit_left``).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from . import settings as app_settings
from .cottage import deg_text, room_label
from .visit import in_night

log = logging.getLogger(__name__)

CHECK_INTERVAL = 60.0
OFFLINE_AFTER = 1800.0
PUMP_AFTER = 1800.0
SENSOR_AFTER = 3600.0
HEATER_AFTER = 1800.0
COLD_CLEAR_MARGIN = 1.0
HUMIDITY_AFTER = 86400.0       # a damp day, not a damp hour
HUMIDITY_CLEAR_MARGIN = 5.0    # %-points below the limit
CO2_LIMIT = 1200
CO2_CLEAR = 1000
CO2_AFTER = 600.0
CO2_QUIET = ("22:00", "08:00")   # Helsinki time: nobody wants to air at night
VISIT_SETTINGS = {"warm": "alert_visit_warm", "late": "alert_visit_late",
                  "left": "alert_visit_left"}
PHONE_PREFIX = "mobile_app_"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Problem:
    key: str
    setting: str               # the switch that turns this alert off
    site: str
    room: str | None
    start: tuple[str, str]     # title, message when it starts
    end: tuple[str, str] | None   # title, message when it is over (None: no notice)
    after: float = 0.0         # seconds it must last before the notice


@dataclass(frozen=True)
class Delivery:
    """Who got one notice. ``reason`` says why nobody did: ``unreachable``
    (no Home Assistant), ``no_phones`` (no phone has the app) or ``failed``
    (every send failed)."""
    sent: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()
    reason: str | None = None


def phone_name(service: str) -> str:
    """notify.mobile_app_ville_s24 -> ville_s24."""
    return service.removeprefix(PHONE_PREFIX)


class HomeAssistantNotifier:
    """Sends a notice to every phone of one Home Assistant gateway."""

    def __init__(self, gateway):
        self._gateway = gateway
        self._phones: list[str] | None = None

    def send(self, title: str, message: str) -> int:
        """Send to every phone; the number of phones reached (0: none)."""
        return len(self.deliver(title, message).sent)

    def deliver(self, title: str, message: str) -> Delivery:
        try:
            phones = [s for s in self._gateway.services("notify") if s.startswith(PHONE_PREFIX)]
        except Exception as error:
            log.warning("Alert not sent: Home Assistant unreachable (%s)", type(error).__name__)
            return Delivery(reason="unreachable")
        if phones != self._phones:
            self._phones = phones
            if phones:
                log.info("Alerts go to %d phone(s) in Home Assistant", len(phones))
            else:
                log.warning("Alert not sent: no phone has the Home Assistant app")
        if not phones:
            return Delivery(reason="no_phones")
        sent, failed = [], []
        for phone in phones:
            try:
                self._gateway.call("notify", phone, {"title": title, "message": message})
                sent.append(phone_name(phone))
            except Exception as error:
                failed.append(phone_name(phone))
                log.warning("Alert to %s failed: %s", phone, type(error).__name__)
        if sent:
            log.info("Alert sent: %s", title)
        return Delivery(tuple(sent), tuple(failed), None if sent else "failed")


class AlertMonitor:
    """Checks the sites every minute and sends notices when problems start and end."""

    def __init__(self, service, notifier, settings, *, reloader=None, energy=None,
                 now=utc_now, interval: float = CHECK_INTERVAL):
        self._service = service
        self._notifier = notifier
        self._reloader = reloader
        self._energy = energy
        self._settings = settings
        self._now = now
        self._interval = interval
        self._since: dict[str, datetime] = {}
        self._open: dict[str, Problem] = {}
        self._pending: dict[str, Problem] = {}   # last seen, for a reading that goes stale
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def send_test(self, site: str) -> Delivery:
        """A test notice from the settings page: which phones got it."""
        label = app_settings.SITE_LABELS.get(site, site)
        title = "Villa Kuppis: testi-ilmoitus"
        message = f"Ilmoitukset tulevat perille. Lähetetty {label}-sivun asetuksista."
        return self._notifier.deliver(title, message)

    # -- checks --

    def problems(self, overrides, units) -> list[Problem]:
        out = []
        for unit in units:
            unavailable = unit["power"] is None and unit["mode"] is None
            if not unavailable and not unit["in_error"]:
                continue
            name = unit["name"]
            text = ("Pumppu ei ole ollut käytettävissä 30 minuuttiin." if unavailable
                    else "Pumppu on ilmoittanut laitevirheestä 30 minuutin ajan.")
            if unavailable and self._reloader is not None and self._reloader.tried:
                text += (" Uusi kirjautuminen MELCloud Homeen ei auttanut; tarkista"
                         " integraatio Home Assistantissa.")
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
            # Old readings prove nothing: a cold room, a device problem or a
            # stale-air reminder stays open, unreported, and a damp or
            # stale-air spell keeps its start.
            return out + [p for k, p in self._open.items()
                          if k.startswith(("cold:", "device:", "humid:", "co2:"))] + [
                self._pending[k] for k in self._since
                if k.startswith(("humid:", "co2:")) and k not in self._open and k in self._pending]
        for room in view["rooms"]:
            label = room_label(room["name"])
            sensor = room.get("temperature")
            fresh = sensor is not None and sensor.get("status", "ok") == "ok"
            if sensor is not None and not fresh:
                out.append(Problem(f"device:sensor:{room['id']}", "alert_device", "cottage",
                                   room["id"],
                                   (f"{label}: lämpötila-anturi ei toimi",
                                    "Huoneen lämpötilaa ei ole saatu tuntiin. Patterit pitävät "
                                    "tavoitteen omalla termostaatillaan."),
                                   (f"{label}: lämpötila-anturi toimii taas",
                                    "Huoneen lämpötila saadaan taas."), SENSOR_AFTER))
            for heater in room.get("heaters") or ():
                name = heater.get("label") or heater.get("device_id")
                if heater.get("disobeying") and heater.get("enabled") is not False:
                    out.append(Problem(
                        f"device:obey:{heater.get('device_id')}", "alert_device", "cottage",
                        room["id"],
                        (f"{label}: patteri {name} ei lämmitä",
                         "Patteri kertoo ottaneensa asetuksen mutta ei lämmitä, eikä "
                         "uudelleen lähettäminen auttanut. Säädä patteria sen omista "
                         "painikkeista tai katkaise siltä hetkeksi virta."),
                        (f"{label}: patteri {name} lämmittää taas",
                         "Patteri lämmittää taas asetuksensa mukaan.")))
                if heater.get("available", True) and heater.get("status") not in (
                        "missing", "unavailable"):
                    continue
                key = f"device:heater:{heater.get('device_id')}"
                if heater.get("enabled") is False:
                    # Switched off in Villa Kuppis (unplugged on purpose):
                    # silent, and an open notice is not ended by it.
                    if key in self._open:
                        out.append(self._open[key])
                    continue
                out.append(Problem(key, "alert_device",
                                   "cottage", room["id"],
                                   (f"{label}: patteri {name} ei vastaa",
                                    "Patteri ei ole ollut käytettävissä 30 minuuttiin. "
                                    "Tarkista sen virta ja verkko."),
                                   (f"{label}: patteri {name} vastaa taas",
                                    "Patteri on taas käytettävissä."), HEATER_AFTER))
            key = f"cold:{room['id']}"
            value = sensor.get("value") if fresh else None
            if value is None:
                if key in self._open:
                    out.append(self._open[key])
                continue
            limit = app_settings.resolve(overrides, "cottage", room["id"])["cold_alert_c"]["value"]
            if value < limit or (key in self._open and value < limit + COLD_CLEAR_MARGIN):
                out.append(Problem(key, "alert_cold", "cottage", room["id"],
                                   (f"{room_label(room['name'])} on kylmä",
                                    f"Lämpötila on {deg_text(value)}, alle hälytysrajan {deg_text(limit)}."),
                                   (f"{room_label(room['name'])} on taas lämpimämpi",
                                    f"Lämpötila on noussut yli {deg_text(limit + COLD_CLEAR_MARGIN)}.")))
        site = app_settings.resolve(overrides, "cottage")
        at_cottage = self._visit_on(cottage)
        for room in view["rooms"]:
            out += self._air_problems(room, site["humidity_limit_pct"]["value"], at_cottage,
                                      cottage)
        return out

    def _visit_on(self, cottage) -> bool:
        """Someone is at the cottage: a visit is on and its arrival has come."""
        active = getattr(cottage, "active_visit", None)
        visit = active() if active is not None else None
        return visit is not None and self._now() >= visit.arrival

    def _air_problems(self, room, humidity_limit: float, at_cottage: bool,
                      cottage=None) -> list[Problem]:
        """A damp room (a day over the limit) and, during a visit, stale air.
        A damp spell starts from the stored readings, so a restart of the
        app does not start the day over."""
        out = []
        label = room_label(room["name"])
        for role, key, over, clear in (
                ("humidity", f"humid:{room['id']}", humidity_limit,
                 humidity_limit - HUMIDITY_CLEAR_MARGIN),
                ("co2", f"co2:{room['id']}", CO2_LIMIT, CO2_CLEAR)):
            if role == "co2" and not at_cottage:
                continue
            sensor = room.get(role)
            value = sensor.get("value") if sensor and sensor.get("status", "ok") == "ok" else None
            known = key in self._since or key in self._open
            if value is None:
                if known:    # an old reading neither starts nor ends it
                    out.append(self._open.get(key) or self._pending[key])
                continue
            if not (value > over or (known and value >= clear)):
                continue
            history = getattr(cottage, "above_since", None)
            if role == "humidity" and not known and history is not None:
                try:
                    since = history(room["id"], "humidity", over,
                                    timedelta(seconds=HUMIDITY_AFTER + 3600))
                except Exception as error:
                    log.warning("Humidity history failed: %s", type(error).__name__)
                    since = None
                if since is not None:
                    self._since[key] = min(since, self._now())
            if role == "humidity":
                problem = Problem(
                    key, "alert_humidity", "cottage", room["id"],
                    (f"{label} on kostea",
                     f"Ilmankosteus on ollut vuorokauden yli {over:g} % (nyt {value:.0f} %). "
                     "Jatkuva kosteus on homeriski: lämmitä tai tuuleta."),
                    (f"{label}: kosteus on laskenut",
                     f"Ilmankosteus on laskenut alle {clear:g} %."), HUMIDITY_AFTER)
            else:
                problem = Problem(
                    key, "alert_co2", "cottage", room["id"],
                    (f"Tuuleta: {label}",
                     f"Hiilidioksidia on {value:.0f} ppm. Raikkaassa sisäilmassa sitä on alle "
                     f"{CO2_CLEAR} ppm."), None, CO2_AFTER)
            self._pending[key] = problem
            out.append(problem)
        return out

    def check(self) -> None:
        now = self._now()
        overrides = self._settings.overrides()

        def enabled(problem):
            return app_settings.resolve(overrides, problem.site, problem.room)[problem.setting]["value"]

        units = self._service.view()["units"]
        if self._reloader is not None:
            self._reloader.check(units, app_settings.resolve(overrides, "home")["pump_relogin"]["value"])
        try:
            self._visit_notices(overrides)
        except Exception as error:
            log.warning("Visit notices failed: %s", type(error).__name__)
        if self._energy is not None:
            try:
                self._energy.check(self._service.store)
            except Exception as error:
                log.warning("Pump energy read failed: %s", type(error).__name__)
        current = {p.key: p for p in self.problems(overrides, units)}
        for key in [k for k in self._since if k not in current]:
            del self._since[key]
        for key, problem in current.items():
            since = self._since.setdefault(key, now)
            if key in self._open or not enabled(problem):
                continue
            if problem.setting == "alert_co2" and in_night(now, CO2_QUIET):
                continue     # sent in the morning if the air is still stale
            if (now - since).total_seconds() >= problem.after and self._notifier.send(*problem.start):
                self._open[key] = problem
        for key in [k for k in self._open if k not in current]:
            problem = self._open[key]
            if not enabled(problem) or problem.end is None or self._notifier.send(*problem.end):
                del self._open[key]

    def _visit_notices(self, overrides) -> None:
        """Notices about the cottage visit, each once per visit."""
        cottage = getattr(self._service, "cottage", None)
        if cottage is None:
            return
        values = app_settings.resolve(overrides, "cottage")
        for visit_id, kind, title, message in cottage.visit_notices():
            if cottage.visit_notice_sent(visit_id, kind):
                continue
            if not values[VISIT_SETTINGS[kind]]["value"]:
                cottage.mark_visit_notice(visit_id, kind)   # off now: not later either
            elif self._notifier.send(title, message):
                cottage.mark_visit_notice(visit_id, kind)

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
