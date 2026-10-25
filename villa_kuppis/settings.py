"""In-app settings (card F3.5).

Every control setting has a default (docs/spot-price.md) and can be
overridden per site or per room; a room override wins over the site
override, which wins over the default. Overrides live in the
``setting`` table of ``pumps.db`` (so the daily backup covers them) and
are read fresh by every plan, so a change takes effect in the next plan
without a restart.

Scopes are ``site:<site>`` and ``room:<site>/<room>``. The pure helpers
at the bottom (``resolve``, ``plan_settings``) turn the stored overrides
into the settings object that ``planning.plan_day`` consumes, so the plan
code itself stays a pure function.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

SITES = ("home", "cottage")
SITE_LABELS = {"home": "Koti", "cottage": "Mökki"}

#: The control kind is a property of the site: the home cools, the cottage heats.
SITE_KIND = {"home": "cool", "cottage": "heat"}

#: Keys replaced on 2026-10-25 (audit 2.2); stored overrides are migrated once.
LEGACY_KEYS = ("kind", "precool_enabled", "cool_optimizer",
               "cool_off_when_expensive", "cool_manage_power")
_PRICE_ON = ("fixed", "optimized")

MAX_ACTOR_LENGTH = 200
MIGRATION_ACTOR = "Asetusten päivitys"
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_SCOPE_RE = re.compile(r"^(site:(home|cottage)|room:(home|cottage)/[^/\x00-\x1f\x7f]{1,100})$")


@dataclass(frozen=True)
class Definition:
    """One setting: key, Finnish label, group, type, default and bounds."""

    key: str
    label: str
    group: str
    type: str                      # number | bool | time | choice
    default: object
    unit: str = ""
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[tuple[str, str], ...] = ()
    help: str = ""                 # Finnish explanation shown as a tooltip
    kinds: tuple[str, ...] = ("cool", "heat")   # control kinds it applies to
    # Shown only while another setting has one of these values: (key, values).
    shown_if: tuple[str, tuple[object, ...]] | None = None

    def public(self) -> dict:
        shown_if = None if self.shown_if is None else {
            "key": self.shown_if[0], "values": list(self.shown_if[1])}
        return {"key": self.key, "help": self.help, "kinds": list(self.kinds), "label": self.label, "group": self.group,
                "shown_if": shown_if,
                "type": self.type, "default": self.default,
                "unit": self.unit, "min": self.minimum, "max": self.maximum,
                "choices": [{"value": v, "label": l} for v, l in self.choices]}


DEFINITIONS: tuple[Definition, ...] = (
    Definition("cool_max_c", "Enimmäislämpötila", "Jäähdytys", "number", 25.0,
               "°C", 15, 35, help="Lämpötila, johon huone saa nousta. Jäähdytys pitää huoneen tässä tai alle, myös kalliilla hinnalla.", kinds=("cool",)),
    Definition("price_control", "Hintaohjaus", "Jäähdytys", "choice", "optimized",
               choices=(("off", "Pois"), ("fixed", "Kiinteät rajat"), ("optimized", "Optimoitu")),
               help="Pois: tavoite on aina enimmäislämpötila, eikä hinta vaikuta (sopii lämmityskaudelle, jotta jäähdytys ei kilpaile pattereiden kanssa). Kiinteät rajat: halvalla hinnalla esijäähdytetään, kalliilla tavoite on enimmäislämpötila ja pumppu saa olla sammuksissa. Optimoitu: jäähdytys suunnitellaan koko tunnetulle hintajaksolle huoneen opitun jäähtymisnopeuden ja sääennusteen avulla, eikä tulos ole kiinteitä rajoja kalliimpi. Optimointi alkaa, kun huoneen nopeus on opittu; siihen asti ja poissa-tilassa käytetään kiinteitä rajoja.",
               kinds=("cool",)),
    Definition("precool_c", "Esijäähdytyslämpötila", "Jäähdytys", "number",
               21.0, "°C", 15, 30, help="Tavoite halvalla hinnalla: huone jäähdytetään etukäteen näin viileäksi, jotta pumppu voi olla kalliilla hinnalla pidempään sammuksissa. Ei voi ylittää enimmäislämpötilaa.",
               kinds=("cool",), shown_if=("price_control", _PRICE_ON)),
    Definition("low_price_c_kwh", "Halpa hinta", "Jäähdytys", "number", 5.0,
               "c/kWh", -50, 500, help="Pörssihinta, jonka alle sähkö on halpaa: silloin huone jäähdytetään esijäähdytyslämpötilaan. Hintoihin lisätään ALV, jos ALV-kytkin on päällä.",
               kinds=("cool",), shown_if=("price_control", _PRICE_ON)),
    Definition("high_price_c_kwh", "Kallis hinta", "Jäähdytys", "number",
               15.0, "c/kWh", -50, 500, help="Pörssihinta, josta alkaen sähkö on kallista: tavoite on silloin enimmäislämpötila, ja jos automaatio saa ohjata pumpun käyntiä, pumppu sammutetaan, kun huone on 0,5 °C enimmäislämpötilan alla. Hintoihin lisätään ALV, jos ALV-kytkin on päällä.",
               kinds=("cool",), shown_if=("price_control", _PRICE_ON)),
    Definition("cool_power", "Automaatio saa käynnistää ja sammuttaa pumpun",
               "Jäähdytys", "bool", True,
               help="Kun päällä, automaatio sammuttaa Jäähdytys-tilassa olevan pumpun, kun huone on 0,5 °C tavoitteen alla (kalliilla hinnalla enimmäislämpötilan alla), ja käynnistää sen, kun huone ylittää tavoitteen (kalliilla hinnalla enimmäislämpötilan). Pumppua ei käynnistetä, jos jäähdytykselle ei ole tarvetta. Jos haluat pitää pumpun pois käytöstä pysyvästi, vaihda sen toimintatila muuksi kuin Jäähdytys tai kytke tämä pois huoneen asetuksista.",
               kinds=("cool",)),
    Definition("min_off_minutes", "Vähimmäisaika käynnistysten välillä", "Jäähdytys", "number",
               30, "min", 0, 240, help="Automaatio ei käynnistä tai sammuta pumppua, ennen kuin näin kauan on kulunut edellisestä käynnistyksestä tai sammutuksesta (kompressorin suojaus).",
               kinds=("cool",), shown_if=("cool_power", (True,))),
    Definition("manual_hold_hours", "Käsiohjauksen tauko", "Jäähdytys", "number",
               3.0, "h", 0, 24, help="Kun pumpun virtaa, tavoitelämpötilaa tai toimintatilaa muutetaan käsin (tässä sovelluksessa, MELCloud Homessa tai kaukosäätimellä), automaatio jättää pumpun rauhaan näin moneksi tunniksi. Kortin Palauta automaatio -painike lopettaa tauon heti. 0 = ei taukoa.",
               kinds=("cool",)),
    Definition("away_max_c", "Enimmäislämpötila poissa kotoa", "Poissa kotoa",
               "number", 28.0, "°C", 15, 40, help="Poissa kotoa -tilassa jäähdytetään vasta tähän lämpötilaan, eikä esijäähdytetä. Ei voi olla pienempi kuin enimmäislämpötila.", kinds=("cool",)),
    Definition("night_enabled", "Yötila käytössä", "Yötila", "bool", False, help="Yötilassa pumpun puhallus asetetaan hiljaisimmalle nopeudelle yötilan aikana, ja aiempi puhallusasetus palautetaan aamulla, ellet ole muuttanut sitä itse. Ei riipu hinnoista.", kinds=("cool",)),
    Definition("night_start", "Yötilan alku", "Yötila", "time", "22:00", help="Yötilan alkamisaika (Suomen aika).", kinds=("cool",), shown_if=("night_enabled", (True,))),
    Definition("night_end", "Yötilan loppu", "Yötila", "time", "07:00", help="Yötilan päättymisaika (Suomen aika); voi olla seuraavana päivänä.", kinds=("cool",), shown_if=("night_enabled", (True,))),
    Definition("vat_enabled", "Lisää ALV sähkönhintaan", "Pörssihinta", "bool",
               False, help="Lisää ALV sähkön pörssihintaan. Vaikuttaa hintakäyrään, energiakustannuksiin ja halpa/kallis-rajojen vertailuun, joten rajat annetaan ALV:llisina."),
    Definition("vat_percent", "ALV-prosentti", "Pörssihinta", "number", 25.5,
               "%", 0, 100, help="Sähkön ALV-prosentti, jota käytetään, kun ALV-kytkin on päällä.", shown_if=("vat_enabled", (True,))),
    Definition("heat_base_c", "Perustaso (lämmitys)", "Lämmitys", "number",
               5.0, "°C", 0, 30, help="Lämmityksen perustavoite, kun mikään hintaraja ei täyty. Mökin ohjaus on vielä rakenteilla, joten asetus ei toistaiseksi ohjaa laitteita.", kinds=("heat",)),
    Definition("heat_rule1_price_c_kwh", "Hintaraja 1: hinta alle", "Lämmitys",
               "number", 8.0, "c/kWh", -50, 500, help="Kun hinta on tämän alle, lämmitetään hintarajan 1 tavoitelämpötilaan (suunniteltu mökin ohjaus; ei vielä käytössä).", kinds=("heat",)),
    Definition("heat_rule1_c", "Hintaraja 1: tavoitelämpötila", "Lämmitys",
               "number", 8.0, "°C", 0, 30, help="Tavoitelämpötila, kun hinta on hintarajan 1 alle (ei vielä käytössä).", kinds=("heat",)),
    Definition("heat_rule2_price_c_kwh", "Hintaraja 2: hinta alle", "Lämmitys",
               "number", 5.0, "c/kWh", -50, 500, help="Kun hinta on tämän alle, lämmitetään hintarajan 2 tavoitelämpötilaan; ylempi täsmäävä tavoite voittaa (ei vielä käytössä).", kinds=("heat",)),
    Definition("heat_rule2_c", "Hintaraja 2: tavoitelämpötila", "Lämmitys",
               "number", 12.0, "°C", 0, 30, help="Tavoitelämpötila, kun hinta on hintarajan 2 alle (ei vielä käytössä).", kinds=("heat",)),
    Definition("smart_enabled", "Älykäs ylläpitolämmitys", "Lämmitys", "bool",
               False, help="Suunniteltu älykäs ylläpitolämmitys: lämmitetään halvoilla tunneilla varaavasti vähimmäis- ja enimmäislämpötilan välillä. Ei vielä käytössä.", kinds=("heat",)),
    Definition("smart_min_c", "Vähimmäislämpötila", "Lämmitys", "number", 5.0,
               "°C", 0, 30, help="Älykkään ylläpitolämmityksen alaraja: tämän alle lämpötila ei saa laskea (ei vielä käytössä).", kinds=("heat",)),
    Definition("smart_max_c", "Suurin varaava lämpötila", "Lämmitys",
               "number", 12.0, "°C", 0, 30, help="Älykkään ylläpitolämmityksen yläraja: halvoilla tunneilla lämmitetään enintään tähän (ei vielä käytössä).", kinds=("heat",)),
)
BY_KEY = {d.key: d for d in DEFINITIONS}


def validate_value(key: str, value: object) -> object:
    """Return the normalized value for ``key`` or raise ValueError (Finnish)."""
    definition = BY_KEY.get(key)
    if definition is None:
        raise ValueError("tuntematon asetus")
    if definition.type == "bool":
        if not isinstance(value, bool):
            raise ValueError("arvon on oltava päällä tai pois")
        return value
    if definition.type == "choice":
        if value not in dict(definition.choices):
            raise ValueError("virheellinen valinta")
        return value
    if definition.type == "time":
        if not isinstance(value, str) or not _TIME_RE.match(value):
            raise ValueError("kellonajan muoto on tt:mm")
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("arvon on oltava luku")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError("arvon on oltava äärellinen luku")
    if definition.minimum is not None and number < definition.minimum:
        raise ValueError(f"arvon on oltava vähintään {definition.minimum:g}")
    if definition.maximum is not None and number > definition.maximum:
        raise ValueError(f"arvon on oltava enintään {definition.maximum:g}")
    if isinstance(definition.default, int) and not isinstance(definition.default, bool):
        if number != int(number):
            raise ValueError("arvon on oltava kokonaisluku")
        return int(number)
    return number


def _check_consistent(values: Mapping[str, object]) -> None:
    """Cross-field rules on a fully resolved value set."""
    if values["precool_c"] > values["cool_max_c"]:
        raise ValueError("esijäähdytyslämpötila ei voi ylittää enimmäislämpötilaa")
    if values["away_max_c"] < values["cool_max_c"]:
        raise ValueError("poissaoloraja ei voi olla enimmäislämpötilaa pienempi")
    if values["low_price_c_kwh"] > values["high_price_c_kwh"]:
        raise ValueError("halpa hinta ei voi ylittää kallista hintaa")
    if values["smart_min_c"] > values["smart_max_c"]:
        raise ValueError("vähimmäislämpötila ei voi ylittää suurinta varaavaa lämpötilaa")


def parse_scope(scope: str) -> tuple[str, str | None]:
    """Return ``(site, room or None)``; ValueError for a malformed scope."""
    if not isinstance(scope, str) or not _SCOPE_RE.match(scope):
        raise ValueError("virheellinen kohde")
    if scope.startswith("site:"):
        return scope[5:], None
    site, room = scope[5:].split("/", 1)
    return site, room


def resolve(overrides: Mapping[str, Mapping[str, object]], site: str,
            room: str | None = None) -> dict[str, dict]:
    """Effective values with their source: ``{key: {value, source}}``."""
    out: dict[str, dict] = {}
    site_over = overrides.get(f"site:{site}", {})
    room_over = overrides.get(f"room:{site}/{room}", {}) if room else {}
    for d in DEFINITIONS:
        if d.key in room_over:
            out[d.key] = {"value": room_over[d.key], "source": "room"}
        elif d.key in site_over:
            out[d.key] = {"value": site_over[d.key], "source": "site"}
        else:
            out[d.key] = {"value": d.default, "source": "default"}
    return out


def _values(resolved: Mapping[str, Mapping]) -> dict[str, object]:
    return {k: v["value"] for k, v in resolved.items()}


def plan_settings(overrides: Mapping[str, Mapping[str, object]], site: str,
                  rooms: Sequence[str]) -> dict:
    """Build the ``planning.plan_day`` settings object for one site.

    Home rooms plan cooling (base = maximum temperature, one precool rule
    below the low price); cottage rooms plan heating (base level plus the
    two price rules). Every room is written out explicitly from its
    resolved values, so a room override of any setting reaches the plan.
    """
    def entry(values: Mapping[str, object]) -> dict:
        if SITE_KIND[site] == "cool":
            return {"base_temperature_c": values["cool_max_c"],
                    "rules": [{"kind": "cool",
                               "threshold_c_kwh": values["low_price_c_kwh"],
                               "target_c": values["precool_c"]}]
                    if values["price_control"] != "off" else []}
        return {"base_temperature_c": values["heat_base_c"],
                "rules": [
                    {"kind": "heat",
                     "threshold_c_kwh": values["heat_rule1_price_c_kwh"],
                     "target_c": values["heat_rule1_c"]},
                    {"kind": "heat",
                     "threshold_c_kwh": values["heat_rule2_price_c_kwh"],
                     "target_c": values["heat_rule2_c"]}]}

    site_entry = entry(_values(resolve(overrides, site)))
    return {"site": site_entry,
            "rooms": {room: entry(_values(resolve(overrides, site, room)))
                      for room in rooms}}


def vat_factor(overrides: Mapping[str, Mapping[str, object]], site: str) -> float:
    """Price multiplier for the site: 1 + VAT when VAT is switched on."""
    values = _values(resolve(overrides, site))
    return 1 + float(values["vat_percent"]) / 100 if values["vat_enabled"] else 1.0


class SettingsStore:
    """SQLite-backed overrides. Short-lived connections, safe across threads."""

    def __init__(self, path: str | Path, *, now=None):
        self._path = Path(path)
        self._now = now or (lambda: datetime.now(timezone.utc))
        conn = self._connect()
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS setting ("
                "scope TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, "
                "updated_at TEXT NOT NULL, updated_by TEXT NOT NULL, "
                "PRIMARY KEY (scope, key))")
            _migrate_legacy(conn, self._now().isoformat())
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), timeout=5.0,
                               isolation_level=None)
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def overrides(self) -> dict[str, dict[str, object]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT scope, key, value FROM setting").fetchall()
        finally:
            conn.close()
        out: dict[str, dict[str, object]] = {}
        for scope, key, raw in rows:
            if key in BY_KEY:
                out.setdefault(scope, {})[key] = json.loads(raw)
        return out

    def audit(self) -> dict[str, dict[str, dict]]:
        """``{scope: {key: {updated_at, updated_by}}}`` for the UI."""
        conn = self._connect()
        try:
            rows = conn.execute("SELECT scope, key, updated_at, updated_by "
                                "FROM setting").fetchall()
        finally:
            conn.close()
        out: dict[str, dict[str, dict]] = {}
        for scope, key, at, by in rows:
            out.setdefault(scope, {})[key] = {"updated_at": at,
                                              "updated_by": by}
        return out

    def set(self, scope: str, key: str, value: object, actor: str) -> object:
        """Validate and store an override; ValueError on a bad change."""
        site, room = parse_scope(scope)
        value = validate_value(key, value)
        actor = str(actor)[:MAX_ACTOR_LENGTH]
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                current = self._read(conn)
                current.setdefault(scope, {})[key] = value
                self._check_scope(current, site, room)
                conn.execute(
                    "INSERT INTO setting (scope, key, value, updated_at, "
                    "updated_by) VALUES (?, ?, ?, ?, ?) ON CONFLICT(scope, key) "
                    "DO UPDATE SET value = excluded.value, "
                    "updated_at = excluded.updated_at, "
                    "updated_by = excluded.updated_by",
                    (scope, key, json.dumps(value), self._now().isoformat(),
                     actor))
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()
        return value

    def reset(self, scope: str, key: str) -> None:
        """Remove an override so the inherited value applies again."""
        site, room = parse_scope(scope)
        if key not in BY_KEY:
            raise ValueError("tuntematon asetus")
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("DELETE FROM setting WHERE scope = ? AND key = ?",
                             (scope, key))
                # Removing a site override can expose a room/default pair
                # that no longer fits together; refuse such a reset.
                self._check_site(self._read(conn), site)
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()

    @staticmethod
    def _read(conn) -> dict[str, dict[str, object]]:
        out: dict[str, dict[str, object]] = {}
        for scope, key, raw in conn.execute(
                "SELECT scope, key, value FROM setting"):
            if key in BY_KEY:
                out.setdefault(scope, {})[key] = json.loads(raw)
        return out

    @staticmethod
    def _check_scope(overrides, site: str, room: str | None) -> None:
        if room is not None:
            _check_consistent(_values(resolve(overrides, site, room)))
            return
        SettingsStore._check_site(overrides, site)

    @staticmethod
    def _check_site(overrides, site: str) -> None:
        _check_consistent(_values(resolve(overrides, site)))
        prefix = f"room:{site}/"
        for scope in overrides:
            if scope.startswith(prefix):
                _check_consistent(_values(resolve(overrides, site,
                                                  scope[len(prefix):])))


# Old defaults of the replaced keys, for reading overrides stored before them.
_LEGACY_DEFAULTS = {"precool_enabled": True, "cool_optimizer": True,
                    "cool_off_when_expensive": True, "cool_manage_power": False}


def _merged(old: Mapping[str, object]) -> dict[str, object]:
    """New values from the resolved old ones (audit 2.2).

    Precooling off meant "the maximum only", which is price control off; the
    expensive switch-off then did nothing useful, so power control follows
    the old temperature switch alone. Otherwise either old power switch
    keeps the automation allowed to switch the pump.
    """
    if not old["precool_enabled"]:
        price = "off"
    else:
        price = "optimized" if old["cool_optimizer"] else "fixed"
    power = bool(old["cool_manage_power"])
    if price != "off":
        power = power or bool(old["cool_off_when_expensive"])
    return {"price_control": price, "cool_power": power}


def _migrate_legacy(conn, now_iso: str) -> None:
    """Replace stored legacy overrides with the merged settings, once.

    The new overrides reproduce the old effective behaviour at every scope
    that had one: a site gets an override where the merged value differs
    from the new default, a room where it differs from its site's.
    """
    marks = ",".join("?" * len(LEGACY_KEYS))
    conn.execute("BEGIN IMMEDIATE")
    try:
        rows = conn.execute(
            f"SELECT scope, key, value FROM setting WHERE key IN ({marks})",
            LEGACY_KEYS).fetchall()
        if not rows:
            conn.execute("ROLLBACK")
            return
        legacy: dict[str, dict[str, object]] = {}
        for scope, key, raw in rows:
            legacy.setdefault(scope, {})[key] = json.loads(raw)

        def old_values(site, room=None):
            values = dict(_LEGACY_DEFAULTS)
            values.update(legacy.get(f"site:{site}", {}))
            if room is not None:
                values.update(legacy.get(f"room:{site}/{room}", {}))
            return _merged(values)

        writes = []
        for site in SITES:
            site_new = old_values(site)
            for key, value in site_new.items():
                if value != BY_KEY[key].default:
                    writes.append((f"site:{site}", key, value))
            prefix = f"room:{site}/"
            for scope in legacy:
                if scope.startswith(prefix):
                    room_new = old_values(site, scope[len(prefix):])
                    for key, value in room_new.items():
                        if value != site_new[key]:
                            writes.append((scope, key, value))
        for scope, key, value in writes:
            conn.execute(
                "INSERT OR REPLACE INTO setting (scope, key, value, updated_at, "
                "updated_by) VALUES (?, ?, ?, ?, ?)",
                (scope, key, json.dumps(value), now_iso, MIGRATION_ACTOR))
        conn.execute(f"DELETE FROM setting WHERE key IN ({marks})", LEGACY_KEYS)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
