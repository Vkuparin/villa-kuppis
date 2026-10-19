"""Finnish web app for the home pumps (card F2.3, front end).

Serves one self-contained page (inline CSS and JS, no external assets,
fully offline) plus a small JSON API on top of PumpService:

* GET  /              the Finnish app
* GET  /api/view      unit states, pending slots and service status
* GET  /api/events    the event log (newest first)
* GET  /api/history   downsampled time series (card F3.3)
* GET  /api/identity  the trusted actor of this connection (card F3.2)
* POST /api/command   {"unit", "action", "value"} -> the service

The page shows the cloud-reported state and the requested change
separately until the device confirms it (docs/ui-fi.md). Format is
fi-FI: decimal comma, Celsius, 24-hour clock, Europe/Helsinki times.

Identity (card F3.2): the server always binds loopback only. Loopback
hosts (127.0.0.1:port, localhost:port) are the local machine in every
mode: same-origin HTTP and the local actor, Tailscale identity headers
ignored. With a configured public origin -- the HTTPS ts.net origin of
Tailscale Serve -- the exact public Host is additionally required for
non-loopback requests, plus exactly one trusted Tailscale-User-Login
header on every route (identity-less requests are refused), and an
Origin header, when present, must match the configured origin exactly
(duplicate Origins are refused). The optional Tailscale-User-Name
header (RFC 2047) supplies the display name. The browser body never
chooses the actor.

Offline-first: importing this module and ``--help`` never touch the
network. Demo mode runs the whole app against the in-memory FakeClient;
live mode uses the environment credentials of make_live_service
(development mode; F3.2 moves them to the Windows credential store).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from email.header import decode_header
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast
from urllib.parse import parse_qs, urlsplit

from .pump_probe import FakeClient, _unit_payload
from .pump_service import MAX_ACTOR_LENGTH, PumpService, make_live_service

DEFAULT_HOST = "127.0.0.1"
# The actor used for every request that arrives without a trusted remote
# identity (i.e. directly on the loopback interface).
DEFAULT_LOCAL_ACTOR = "Paikallinen käyttäjä"
DEFAULT_PORT = 8377
MAX_BODY_BYTES = 64 * 1024

DEMO_UNITS = (
    _unit_payload("demo-1", "Demo-yksikkö 1", mode="Cool", power="True",
                  set_temp="23.5", room="26", fan="Auto"),
    _unit_payload("demo-2", "Demo-yksikkö 2", mode="Cool", power="False",
                  set_temp="22", room="27", fan="Two"),
    _unit_payload("demo-3", "Demo-yksikkö 3", mode="Fan", power="True",
                  set_temp="23", room="25", fan="One"),
)


def make_demo_service(data_dir, *, poll_interval: float = 5.0,
                      stale_after: float = 60.0,
                      readback_polls: int = 2, readback_delay: float = 1.0,
                      command_timeout: float = 10.0) -> PumpService:
    """Offline service: the real pipeline against the in-memory FakeClient."""
    client = FakeClient([dict(p) for p in DEMO_UNITS])
    return PumpService(
        data_dir, client=client, poll_interval=poll_interval,
        stale_after=stale_after, readback_polls=readback_polls,
        readback_delay=readback_delay, command_timeout=command_timeout,
    )


# --- trusted identity (card F3.2) -------------------------------------------------

TAILNET_SUFFIX = ".ts.net"
_HOSTNAME_LABEL = re.compile(r"[a-z0-9-]+\Z")


def _is_dns_hostname(name: str) -> bool:
    """True for an ASCII DNS hostname (already lower-cased)."""
    labels = name.split(".")
    if not (2 <= len(labels) <= 128) or len(name) > 253:
        return False
    for label in labels:
        if not label or len(label) > 63 or label[0] == "-" or label[-1] == "-":
            return False
        if not _HOSTNAME_LABEL.match(label):
            return False
    return True


def _validate_public_origin(origin: str) -> tuple[str, str]:
    """Validate the configured public origin.

    Returns ``(origin, authority)`` where origin is the normalized
    ``https://host[:port]`` URL to require in the Origin header and
    authority is the exact ``host[:port]`` to require in the Host header.
    Only HTTPS ts.net tailnet hostnames are accepted: no userinfo, no
    path/query/fragment, no wildcard or suffix trust.
    """
    if not isinstance(origin, str) or not origin.strip():
        raise ValueError("the public origin must be a non-empty HTTPS URL")
    try:
        parts = urlsplit(origin.strip())
    except ValueError as err:
        raise ValueError(f"the public origin is not a valid URL: {err}") from err
    if parts.scheme.lower() != "https":
        raise ValueError("the public origin must use HTTPS")
    if parts.username is not None or parts.password is not None:
        raise ValueError("the public origin must not contain credentials")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ValueError("the public origin must not contain a path, "
                         "query or fragment")
    host = parts.hostname
    if not host:
        raise ValueError("the public origin has no hostname")
    host = host.lower()
    if not _is_dns_hostname(host):
        raise ValueError("the public origin hostname is not a valid DNS name")
    if not host.endswith(TAILNET_SUFFIX):
        raise ValueError("the public origin must be a ts.net tailnet hostname")
    if parts.port is not None:
        return f"https://{host}:{parts.port}", f"{host}:{parts.port}"
    return f"https://{host}", host


def _decode_rfc2047(value: str) -> str:
    """Decode an optional RFC 2047 encoded-word header value."""
    try:
        parts = decode_header(value)
    except Exception:
        return value
    out = []
    for chunk, charset in parts:
        if isinstance(chunk, bytes):
            try:
                out.append(chunk.decode(charset or "utf-8", errors="replace"))
            except LookupError:
                out.append(chunk.decode("utf-8", errors="replace"))
        else:
            out.append(chunk)
    return "".join(out)


def _has_control_chars(text: str) -> bool:
    return any(ord(c) < 0x20 or ord(c) == 0x7F for c in text)


# --- the page ---------------------------------------------------------------

_PAGE = """<!doctype html>
<html lang="fi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pumput</title>
<style>
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; font: 16px/1.45 system-ui, "Segoe UI", sans-serif;
       background: #f2f4f6; color: #1a1d21; }
header.top { display: flex; flex-wrap: wrap; gap: .5rem 1.25rem;
             align-items: baseline; padding: .9rem 1.25rem; background: #fff;
             border-bottom: 1px solid #d9dee3; }
h1 { font-size: 1.35rem; margin: 0; }
.badge { display: inline-block; padding: .18rem .6rem; border-radius: .5rem;
         border: 1px solid #777; font-size: .85rem; }
.badge.ok   { color: #0b6b2a; border-color: #0b6b2a; background: #e8f6ec; }
.badge.warn { color: #8a5b00; border-color: #8a5b00; background: #fdf3dd; }
.badge.err  { color: #a11d1d; border-color: #a11d1d; background: #fdecec; }
.badge.info { color: #0b3f9e; border-color: #0b3f9e; background: #eef4ff; }
#identity { max-width: 240px; overflow: hidden; text-overflow: ellipsis;
           white-space: nowrap; font-size: .85rem; color: #44505c;
           background: #eef1f4; border: 1px solid #d7dde3;
           border-radius: .5rem; padding: .18rem .6rem; }
#last-updated { font-size: .85rem; color: #55606b; }
#demo-banner { display: none; padding: .6rem 1.25rem; background: #fdf3dd;
               border-bottom: 1px solid #e8d9a8; color: #6b4d00; }
body[data-demo="1"] #demo-banner { display: block; }
main { padding: 1.25rem; max-width: 1100px; margin: 0 auto; }
#cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(290px, 1fr));
         gap: 1rem; margin-bottom: 2rem; }
.card { background: #fff; border: 1px solid #d9dee3; border-radius: .75rem;
        padding: 1rem 1.1rem; }
.card.stale { border-color: #d9b96a; background: #fffdf6; }
.card-head { display: flex; justify-content: space-between; align-items: baseline;
             gap: .75rem; flex-wrap: wrap; margin-bottom: .5rem; }
.card-head h3 { margin: 0; font-size: 1.1rem; }
.row { display: flex; justify-content: space-between; align-items: center;
       gap: .6rem; padding: .3rem 0; flex-wrap: wrap; }
.row .label { color: #44505c; }
.value { font-variant-numeric: tabular-nums; }
.value.big { font-size: 1.3rem; font-weight: 600; }
button, select { min-height: 44px; min-width: 44px; font-size: 1rem;
                 border: 1px solid #9aa4ae; border-radius: .5rem;
                 background: #fff; color: #1a1d21; padding: 0 .7rem;
                 cursor: pointer; }
select { cursor: pointer; min-width: 110px; }
button:disabled, select:disabled { opacity: .55; cursor: not-allowed; }
button:focus-visible, select:focus-visible, a:focus-visible {
  outline: 3px solid #0b5fff; outline-offset: 2px; }
.stepper { display: inline-flex; gap: .4rem; }
.stepper button { min-width: 44px; font-size: 1.15rem; font-weight: 700; }
.pending { margin-top: .8rem; padding: .55rem .75rem; border: 1px dashed #0b5fff;
           border-radius: .5rem; background: #eef4ff; color: #0b3f9e; }
section.events { background: #fff; border: 1px solid #d9dee3;
                 border-radius: .75rem; padding: 1rem 1.1rem; }
section.events h2 { margin: 0 0 .6rem; font-size: 1.15rem; }
section.history { background: #fff; border: 1px solid #d9dee3;
                  border-radius: .75rem; padding: 1rem 1.1rem;
                  margin-bottom: 1rem; }
section.history h2 { margin: 0 0 .6rem; font-size: 1.15rem; }
.hist-controls { display: flex; flex-wrap: wrap; gap: .6rem 1.25rem;
                 align-items: end; margin-bottom: .75rem; }
.hist-controls label { display: flex; flex-direction: column; gap: .2rem;
                       font-size: .85rem; color: #44505c; }
#hist-status { font-size: .85rem; color: #55606b; }
.hist-chart { width: 100%; }
.hist-chart svg { display: block; width: 100%; height: auto; }
.hist-legend { list-style: none; display: flex; flex-wrap: wrap;
               gap: .4rem 1.1rem; padding: .4rem 0 0; margin: 0;
               font-size: .85rem; color: #44505c; }
.hist-legend .swatch { display: inline-block; width: 1.4rem; height: .28rem;
                       margin-right: .35rem; vertical-align: middle; }
.hist-empty { color: #55606b; padding: 1rem 0; }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; padding: .45rem .6rem; border-bottom: 1px solid #e3e8ec;
         vertical-align: top; }
th { color: #44505c; font-weight: 600; }
td.result-confirmed { color: #0b6b2a; }
td.result-failed { color: #a11d1d; }
td.result-pending { color: #0b3f9e; }
.detail { display: block; font-size: .85rem; color: #55606b; }
#no-events { color: #55606b; }
#alerts { position: fixed; left: 1rem; right: 1rem; bottom: 1rem;
          display: flex; flex-direction: column; gap: .5rem; z-index: 10; }
.alert { background: #fdecec; border: 1px solid #a11d1d; color: #7a1414;
         padding: .6rem .9rem; border-radius: .5rem; }
.alert.info { background: #eef4ff; border-color: #0b3f9e; color: #0b3f9e; }
@media (max-width: 560px) {
  .row { flex-direction: column; align-items: stretch; gap: .35rem; }
  .row .label { order: 1; }
  .row > *:not(.label) { order: 2; }
  th, td { padding: .4rem .45rem; font-size: .92rem; }
}
</style>
</head>
<body data-demo="0">
<header class="top">
  <h1>Pumput</h1>
  <span id="identity" class="identity" title="Kirjautunut käyttäjä"></span>
  <span id="service-status" role="status"><span class="badge info">Yhteyttä muodostetaan…</span></span>
  <span id="last-updated"></span>
</header>
<div id="demo-banner">Esittelytila: näytetyt arvot ovat keksittyjä ja toiminnot
ohjaavat vain esittelyyksiköitä.</div>
<main>
  <section id="cards" aria-label="Pumppulaitteet"></section>
  <section class="history" aria-label="Historia ja kaaviot">
    <h2>Historia</h2>
    <div class="hist-controls">
      <label>Huone
        <select id="hist-unit" aria-label="Huone"></select>
      </label>
      <label>Ajanjakso
        <select id="hist-range" aria-label="Ajanjakson kesto">
          <option value="day" selected>Päivä</option>
          <option value="week">Viikko</option>
          <option value="month">Kuukausi</option>
          <option value="year">Vuosi</option>
        </select>
      </label>
      <label>Ulkolämpötila
        <select id="hist-site" aria-label="Ulkolämpötilan lähde">
          <option value="home" selected>Koti</option>
          <option value="cottage">Mökki</option>
        </select>
      </label>
      <span id="hist-status" role="status"></span>
    </div>
    <div id="hist-chart" class="hist-chart" role="img"
         aria-label="Lämpötilojen ja sähkönhinnan kaavio"></div>
    <ul id="hist-legend" class="hist-legend"></ul>
    <p class="detail">Ulkolämpötila: Ilmatieteen laitoksen havaintoasema,
      <a href="https://en.ilmatieteenlaitos.fi/open-data-licence">avoin data (CC BY 4.0)</a>.
      Sähkönhinta: Elering. Käyrät on harvennettu näytölle.</p>
  </section>
  <section class="events" aria-label="Tapahtumat">
    <h2>Tapahtumat</h2>
    <table>
      <thead>
        <tr><th>Milloin</th><th>Yksikkö</th><th>Kuka</th><th>Muutos</th><th>Tulos</th></tr>
      </thead>
      <tbody id="event-rows"><tr><td colspan="5" id="no-events">Ei tapahtumia.</td></tr></tbody>
    </table>
  </section>
</main>
<div id="alerts" role="alert" aria-live="assertive"></div>
<script>
"use strict";
const MODE_NAMES = { Cool: "Jäähdytys", Heat: "Lämmitys", Dry: "Kuivaus",
                     Fan: "Puhallus", Automatic: "Automaattinen" };
const FAN_NAMES = { Auto: "Automaattinen", One: "1", Two: "2", Three: "3",
                    Four: "4", Five: "5" };
const MODES = ["Cool", "Heat", "Dry", "Fan", "Automatic"];
const FAN_SPEEDS = ["Auto", "One", "Two", "Three", "Four", "Five"];
const RESULT_TEXT = { pending: "Lähetetään…", confirmed: "Vahvistettu",
                      failed: "Muutos epäonnistui" };
const POLL_MS = 5000;

const $cards = document.getElementById("cards");
const $status = document.getElementById("service-status");
const $updated = document.getElementById("last-updated");
const $rows = document.getElementById("event-rows");
const $alerts = document.getElementById("alerts");
const $histUnit = document.getElementById("hist-unit");
const $histRange = document.getElementById("hist-range");
const $histSite = document.getElementById("hist-site");
const $histChart = document.getElementById("hist-chart");
const $histStatus = document.getElementById("hist-status");
const $histLegend = document.getElementById("hist-legend");

const tempFmt = new Intl.NumberFormat("fi-FI",
  { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const clockFmt = new Intl.DateTimeFormat("fi-FI",
  { timeZone: "Europe/Helsinki", hour: "2-digit", minute: "2-digit" });
const dayFmt = new Intl.DateTimeFormat("fi-FI",
  { timeZone: "Europe/Helsinki", day: "2-digit", month: "2-digit" });

function esc(s) {
  return String(s).replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;",
              "'": "&#39;" }[c]));
}
function fmtTemp(v) {
  return v === null || v === undefined ? "–" : tempFmt.format(v) + " °C";
}
function fmtClock(iso) {
  return clockFmt.format(new Date(iso));
}
function fmtWhen(iso) {
  const d = new Date(iso);
  const s = Math.max(0, (Date.now() - d.getTime()) / 1000);
  if (s < 5) return "juuri nyt";
  if (s < 60) return Math.round(s) + " s sitten";
  if (s < 3600) return Math.round(s / 60) + " min sitten";
  if (s < 86400) return Math.round(s / 3600) + " h sitten";
  return dayFmt.format(d) + " klo " + clockFmt.format(d);
}
function modeName(m) { return m ? (MODE_NAMES[m] || m) : "–"; }
function fanName(f) { return f ? (FAN_NAMES[f] || f) : "–"; }

function changeText(action, requested) {
  switch (action) {
    case "setpoint": return "Tavoitelämpötila → " + fmtTemp(requested);
    case "mode": return "Toimintatila → " + modeName(requested);
    case "fan": return "Puhallus → " + fanName(requested);
    case "power": return requested ? "Laitteen päälle" : "Laitteen pois päältä";
    default: return action;
  }
}

function alert(message, kind) {
  const div = document.createElement("div");
  div.className = "alert" + (kind === "info" ? " info" : "");
  div.textContent = message;
  $alerts.appendChild(div);
  setTimeout(() => div.remove(), 10000);
}

const ERRORS = {
  unknown_action: "Tuntematon toimenpide.",
  not_number: "Arvon täytyy olla luku.",
  no_mode: "Laite ei ole ilmoittanut toimintatilaa.",
  fan_mode: "Tavoitelämpötilaa ei voi säätää puhallustilassa.",
  no_bounds: "Laite ei ole ilmoittanut lämpötilarajoja tälle tilalle.",
  out_of_bounds: "Arvo on laitteen sallitun alueen ulkopuolella.",
  step: "Arvo ei ole laitteen lämpötilavaiheiden mukainen.",
  bad_power: "Virheellinen arvo.",
  bad_mode: "Laite ei tue tätä toimintatilaa.",
  bad_fan: "Laite ei tue tätä puhallusnopeutta.",
  unknown_unit: "Tuntematon laite.",
  pending: "Aiempi muutos on vielä vahvistumassa; yritä hetken kuluttua.",
  not_running: "Palvelu ei ole käynnissä."
};
function commandError(body, fallbackStatus) {
  const base = ERRORS[body.error] || "Muutos epäonnistui (virhekoodi " +
    (body.error || fallbackStatus) + ").";
  return body.detail ? base + " " + body.detail : base;
}

function unitStep(unit) {
  const caps = unit.caps;
  if (caps && caps.half_step === false) return 1;
  return 0.5;
}
function setpointAllowed(unit) {
  if (unit.mode === null) return false;
  if (unit.mode === "Fan") return false;
  const caps = unit.caps;
  let lo = null, hi = null;
  if (caps) {
    if (unit.mode === "Heat") { lo = caps.min_heat; hi = caps.max_heat; }
    else if (unit.mode === "Cool" || unit.mode === "Dry") {
      lo = caps.min_cool; hi = caps.max_cool; }
    else if (unit.mode === "Automatic") { lo = caps.min_auto; hi = caps.max_auto; }
  }
  if (lo === null || hi === null || unit.setpoint === null) return false;
  return true;
}
function modeOptions(unit) {
  const caps = unit.caps;
  return MODES.filter((m) => !(caps && caps.modes && caps.modes[m] === false));
}
function fanOptions(unit) {
  const caps = unit.caps;
  const list = [];
  if (!(caps && caps.auto_fan === false)) list.push("Auto");
  const count = caps && caps.fan_speeds ? caps.fan_speeds : 5;
  for (let i = 1; i <= count; i++) list.push(FAN_SPEEDS[i]);
  return list;
}

function cardHtml(unit) {
  const pending = unit.pending;
  const dis = pending ? " disabled" : "";
  const step = unitStep(unit);
  const canStep = setpointAllowed(unit) && !pending;
  const modes = modeOptions(unit);
  const fans = fanOptions(unit);
  const powerLabel = unit.power === true ? "Päällä" : "Pois päältä";
  const rows = [];
  rows.push(
    '<div class="row"><span class="label">Käynti</span>' +
    '<button data-action="power" data-unit="' + esc(unit.id) + '" data-value="' +
    (unit.power === true ? "false" : "true") + '"' + dis + ">" +
    esc(powerLabel) + "</button></div>"
  );
  rows.push(
    '<div class="row"><span class="label">Mitattu lämpötila</span>' +
    '<span class="value big">' + fmtTemp(unit.room_temp) + "</span></div>"
  );
  rows.push(
    '<div class="row"><span class="label">Tavoitelämpötila</span>' +
    '<span class="value">' + fmtTemp(unit.setpoint) + "</span>" +
    '<span class="stepper">' +
    '<button data-action="setpoint" data-unit="' + esc(unit.id) + '" data-delta="' +
    (-step) + '" aria-label="Laske tavoitelämpötilaa"' +
    (canStep ? "" : " disabled") + ">&minus;</button>" +
    '<button data-action="setpoint" data-unit="' + esc(unit.id) + '" data-delta="' +
    step + '" aria-label="Nosta tavoitelämpötilaa"' +
    (canStep ? "" : " disabled") + ">+</button>" +
    "</span></div>"
  );
  rows.push(
    '<div class="row"><span class="label">Toimintatila</span>' +
    '<select data-action="mode" data-unit="' + esc(unit.id) + '"' + dis + ">" +
    modes.map((m) =>
      '<option value="' + m + '"' + (m === unit.mode ? " selected" : "") + ">" +
      esc(MODE_NAMES[m]) + "</option>").join("") +
    "</select></div>"
  );
  rows.push(
    '<div class="row"><span class="label">Puhallus</span>' +
    '<select data-action="fan" data-unit="' + esc(unit.id) + '"' + dis + ">" +
    fans.map((f) =>
      '<option value="' + f + '"' + (f === unit.fan ? " selected" : "") + ">" +
      esc(FAN_NAMES[f]) + "</option>").join("") +
    "</select></div>"
  );
  const pendingHtml = pending
    ? '<div class="pending">Lähetetään: ' +
      esc(changeText(pending.action, pending.requested)) +
      " &mdash; laite vahvistaa muutoksen</div>"
    : "";
  return (
    '<article class="card' + (unit.fresh ? "" : " stale") + '">' +
    '<header class="card-head"><h3>' + esc(unit.name) + "</h3>" +
    '<span class="badge ' + (unit.fresh ? "ok" : "warn") + '">' +
    (unit.fresh ? "Uusi mittaus" : "Vanhentunut mittaus") + "</span></header>" +
    rows.join("") + pendingHtml + "</article>"
  );
}

function renderView(view) {
  const svc = view.service;
  let cls = "info", text = "Yhteyttä muodostetaan…";
  if (svc.state === "ok") { cls = "ok"; text = "Yhteydessä"; }
  else if (svc.state === "stale") { cls = "warn"; text = "Mittaukset vanhentuneet"; }
  else if (svc.state === "error") { cls = "err"; text = "Yhteysvirhe"; }
  $status.innerHTML = '<span class="badge ' + cls + '">' + esc(text) + "</span>";
  if (svc.state === "error" && svc.error) {
    const d = document.createElement("span");
    d.id = "service-error";
    d.style.cssText = "font-size:.85rem;color:#a11d1d";
    d.textContent = svc.error;
    $status.appendChild(document.createTextNode(" "));
    $status.appendChild(d);
  }
  $updated.textContent = svc.last_ok ? "Päivitetty " + fmtWhen(svc.last_ok) : "";
  document.title = svc.state === "ok" ? "Pumput" : "Pumput — " + text;
  $cards.innerHTML = view.units.map(cardHtml).join("") ||
    '<p class="badge info">Ei yksiköitä tiedossa vielä.</p>';
}

function renderEvents(events) {
  if (!events.length) {
    $rows.innerHTML = '<tr><td colspan="5" id="no-events">Ei tapahtumia.</td></tr>';
    return;
  }
  $rows.innerHTML = events.map((ev) => (
    "<tr>" +
    '<td title="' + esc(fmtClock(ev.ts)) + '">' + esc(fmtWhen(ev.ts)) + "</td>" +
    "<td>" + esc(ev.unit_name) + "</td>" +
    "<td>" + esc(ev.actor) + "</td>" +
    "<td>" + esc(changeText(ev.action, ev.requested)) + "</td>" +
    '<td class="result-' + esc(ev.result) + '">' +
    esc(RESULT_TEXT[ev.result] || ev.result) +
    (ev.detail ? '<span class="detail">' + esc(ev.detail) + "</span>" : "") +
    "</td></tr>"
  )).join("");
}

// --- history graphs (card F3.3): time series, gaps kept as gaps ---
const RANGE_MAX_POINTS = { day: 288, week: 700, month: 900, year: 1000 };
const HIST_SERIES = [
  { key: "room_temp", label: "Huonelämpötila", color: "#0b6b2a",
    scale: "temp", dash: "" },
  { key: "setpoint", label: "Tavoitelämpötila", color: "#a11d1d",
    scale: "temp", dash: "7 5" },
  { key: "outdoor", label: "Ulkolämpötila", color: "#0b3f9e",
    scale: "temp", dash: "2 4" },
];
const PRICE_SERIES = { key: "price", label: "Sähkön hinta (snt/kWh)",
                       color: "#8a5b00", scale: "price", dash: "1 4" };

function svgEl(tag, attrs, text) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const k in attrs) el.setAttribute(k, attrs[k]);
  if (text !== undefined) el.textContent = text;
  return el;
}

function medianDelta(points) {
  if (points.length < 3) return Infinity;
  const d = [];
  for (let i = 1; i < points.length; i++) d.push(points[i][0] - points[i - 1][0]);
  d.sort((a, b) => a - b);
  return d[Math.floor(d.length / 2)];
}

// Build an SVG path, breaking into new subpaths wherever the time jump
// exceeds gapMs so real data gaps stay visible instead of being bridged.
function seriesPath(points, gapMs, X, Y) {
  const segs = [];
  let cur = [], prevT = null;
  const flush = () => { if (cur.length) { segs.push(cur); cur = []; } };
  for (const p of points) {
    const [t, v] = p;
    if (v === null || v === undefined) { flush(); prevT = t; continue; }
    if (prevT !== null && (t - prevT) > gapMs) flush();
    cur.push([X(t), Y(v)]);
    prevT = t;
  }
  flush();
  let d = "";
  for (const seg of segs) {
    d += "M" + seg[0][0].toFixed(1) + " " + seg[0][1].toFixed(1);
    for (let i = 1; i < seg.length; i++)
      d += "L" + seg[i][0].toFixed(1) + " " + seg[i][1].toFixed(1);
  }
  return d;
}

function niceTicks(min, max, count) {
  if (!isFinite(min) || !isFinite(max)) return [];
  if (min === max) return [min];
  const span = max - min;
  const step0 = span / Math.max(1, count);
  const mag = Math.pow(10, Math.floor(Math.log10(step0)));
  const norm = step0 / mag;
  let step;
  if (norm < 1.5) step = 1; else if (norm < 3) step = 2;
  else if (norm < 7) step = 5; else step = 10;
  step *= mag;
  const ticks = [];
  for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-6; v += step)
    ticks.push(Math.round(v * 1e6) / 1e6);
  return ticks;
}

function fmtTimeTick(ms, range) {
  const d = new Date(ms);
  const opts = { timeZone: "Europe/Helsinki" };
  if (range === "year") Object.assign(opts, { month: "short", year: "numeric" });
  else if (range === "month") Object.assign(opts, { day: "numeric", month: "short" });
  else if (range === "week")
    Object.assign(opts, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  else Object.assign(opts, { hour: "2-digit", minute: "2-digit" });
  return new Intl.DateTimeFormat("fi-FI", opts).format(d);
}

function drawHistory(data) {
  $histChart.textContent = "";
  const series = HIST_SERIES
    .map((s) => Object.assign({}, s, {
      pts: (data[s.key] || []).map(([iso, v]) => [new Date(iso).getTime(), v])
    }))
    .filter((s) => s.pts.length > 0);
  const pricePts = (data.price || [])
    .map(([iso, v]) => [new Date(iso).getTime(), v]);
  const allTemp = series.flatMap((s) => s.pts);
  if (!allTemp.length && !pricePts.length) {
    $histChart.innerHTML = '<p class="hist-empty">Ei historiaa tälle valinnalle.</p>';
    $histLegend.textContent = "";
    return;
  }
  const W = Math.max(320, $histChart.clientWidth || 900);
  const H = 300;
  const m = { top: 14, right: 52, bottom: 34, left: 46 };
  const t0 = new Date(data.start).getTime();
  const t1 = new Date(data.end).getTime();
  const maxPts = RANGE_MAX_POINTS[data.range] || 288;

  let tMin = Infinity, tMax = -Infinity;
  for (const p of allTemp)
    if (p[1] != null) { tMin = Math.min(tMin, p[1]); tMax = Math.max(tMax, p[1]); }
  if (!isFinite(tMin)) { tMin = 0; tMax = 1; }
  const tPad = (tMax - tMin) * 0.08 || 0.5;
  tMin -= tPad; tMax += tPad;
  const hasPrice = pricePts.length > 0;
  let pMin = Infinity, pMax = -Infinity;
  if (hasPrice) for (const p of pricePts) { pMin = Math.min(pMin, p[1]); pMax = Math.max(pMax, p[1]); }
  if (hasPrice) { const pp = (pMax - pMin) * 0.12 || 0.25; pMin -= pp; pMax += pp; }

  const X = (t) => m.left + ((t1 === t0) ? 0.5 : (t - t0) / (t1 - t0))
    * (W - m.left - m.right);
  const YT = (v) => m.top + (1 - (v - tMin) / (tMax - tMin))
    * (H - m.top - m.bottom);
  const YP = (v) => m.top + (1 - (v - pMin) / (pMax - pMin))
    * (H - m.top - m.bottom);

  const svg = svgEl("svg", { viewBox: "0 0 " + W + " " + H,
    "xmlns": "http://www.w3.org/2000/svg", role: "img",
    "aria-label": "Lämpötilojen ja sähkönhinnan kaavio" });

  const tTicks = niceTicks(tMin, tMax, 5);
  for (const v of tTicks) {
    const y = YT(v);
    svg.appendChild(svgEl("line", { x1: m.left, x2: W - m.right, y1: y, y2: y,
      stroke: "#e3e8ec", "stroke-width": 1 }));
    svg.appendChild(svgEl("text", { x: m.left - 6, y: y + 4, "text-anchor": "end",
      "font-size": 11, fill: "#44505c" }, tempFmt.format(v) + "\u00b0"));
  }
  if (hasPrice) {
    for (const v of niceTicks(pMin, pMax, 5)) {
      const y = YP(v);
      svg.appendChild(svgEl("text", { x: W - m.right + 6, y: y + 4,
        "text-anchor": "start", "font-size": 11, fill: "#8a5b00" },
        tempFmt.format(v) + "s"));
    }
  }
  const nTime = Math.max(2, Math.min(8, Math.floor((W - m.left - m.right) / 90)));
  for (let i = 0; i <= nTime; i++) {
    const t = t0 + (i / nTime) * (t1 - t0);
    const x = X(t);
    svg.appendChild(svgEl("line", { x1: x, x2: x, y1: m.top, y2: H - m.bottom,
      stroke: "#eef1f4", "stroke-width": 1 }));
    svg.appendChild(svgEl("text", { x: x, y: H - m.bottom + 16,
      "text-anchor": "middle", "font-size": 11, fill: "#44505c" },
      fmtTimeTick(t, data.range)));
  }
  svg.appendChild(svgEl("rect", { x: m.left, y: m.top,
    width: W - m.left - m.right, height: H - m.top - m.bottom,
    fill: "none", stroke: "#c7cfd6" }));

  const expected = (t1 - t0) / maxPts;
  const gapFor = (sampleMs) => Math.max(3 * sampleMs, 2 * expected);

  for (const s of series) {
    const d = seriesPath(s.pts, gapFor(s.key === "outdoor" ? 3600000 : 300000), X, YT);
    if (!d) continue;
    const attrs = { d: d, fill: "none", stroke: s.color, "stroke-width": 2,
      "stroke-linejoin": "round", "stroke-linecap": "round" };
    if (s.dash) attrs["stroke-dasharray"] = s.dash;
    svg.appendChild(svgEl("path", attrs));
  }
  if (hasPrice) {
    const d = seriesPath(pricePts, gapFor(900000), X, YP);
    if (d) svg.appendChild(svgEl("path", {
      d: d, fill: "none", stroke: PRICE_SERIES.color, "stroke-width": 1.5,
      "stroke-dasharray": PRICE_SERIES.dash, "stroke-linejoin": "round" }));
  }

  $histChart.appendChild(svg);

  const items = series.map((s) =>
    '<li><span class="swatch" style="background:' + s.color + '"></span>' +
    esc(s.label) + "</li>");
  if (hasPrice) items.push(
    '<li><span class="swatch" style="background:' + PRICE_SERIES.color +
    '"></span>' + esc(PRICE_SERIES.label) + "</li>");
  $histLegend.innerHTML = items.join("");
}

let histRequest = 0;
let histAbort = null;
async function loadHistory() {
  const request = ++histRequest;
  if (histAbort) histAbort.abort();
  const unit = $histUnit.value;
  const range = $histRange.value;
  const site = $histSite.value;
  if (!unit) { $histChart.innerHTML = ""; $histLegend.textContent = ""; return; }
  histAbort = new AbortController();
  $histStatus.textContent = "Haetaan\u2026";
  try {
    const url = "/api/history?unit=" + encodeURIComponent(unit) +
      "&range=" + encodeURIComponent(range) +
      "&site=" + encodeURIComponent(site);
    const data = await fetch(url, { signal: histAbort.signal }).then((r) => {
      if (!r.ok) throw new Error("http " + r.status);
      return r.json();
    });
    if (request !== histRequest) return;
    drawHistory(data);
    $histStatus.textContent = "";
  } catch (err) {
    if (request === histRequest && err.name !== "AbortError")
      $histStatus.textContent = "Historian haku ep\u00e4onnistui.";
  } finally {
    if (request === histRequest) histAbort = null;
  }
}

function populateUnits(units) {
  const current = $histUnit.value;
  $histUnit.innerHTML = units.map((u) =>
    '<option value="' + esc(u.id) + '">' + esc(u.name) + "</option>").join("");
  if (current && units.some((u) => u.id === current)) $histUnit.value = current;
}

async function refresh() {
  try {
    const [view, events] = await Promise.all([
      fetch("/api/view").then((r) => r.json()),
      fetch("/api/events").then((r) => r.json())
    ]);
    renderView(view);
    renderEvents(events);
  } catch (err) {
    $status.innerHTML = '<span class="badge err">Palveluun ei ole yhteyttä</span>';
  }
}

async function loadIdentity() {
  try {
    const id = await fetch("/api/identity").then((r) => {
      if (!r.ok) throw new Error("http " + r.status);
      return r.json();
    });
    const el = document.getElementById("identity");
    if (!el) return;
    const label = (id.display_name && id.display_name !== id.actor)
      ? id.display_name : id.actor;
    el.textContent = label || "";
    el.title = id.actor || "";
  } catch (err) {
    /* The identity chip stays empty if the request fails. */
  }
}

let sending = false;
async function sendCommand(unitId, action, value) {
  if (sending) return;
  sending = true;
  try {
    const res = await fetch("/api/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ unit: unitId, action: action, value: value })
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      alert(commandError(body, res.status));
    } else {
      refresh();
    }
  } catch (err) {
    alert("Yhteys palveluun epäonnistui.");
  } finally {
    sending = false;
  }
}

function onControl(e) {
  const el = e.target.closest("[data-action]");
  if (!el) return;
  const unitId = el.dataset.unit;
  const action = el.dataset.action;
  if (el.tagName === "SELECT") {
    if (el.value === el.dataset.lastValue) return;
    el.dataset.lastValue = el.value;
    sendCommand(unitId, action, el.value);
    return;
  }
  if (action === "setpoint") {
    const view = lastView;
    const unit = view && view.units.find((u) => u.id === unitId);
    if (!unit || unit.setpoint === null) return;
    const next = Math.round((unit.setpoint + Number(el.dataset.delta)) * 2) / 2;
    sendCommand(unitId, "setpoint", next);
  } else if (action === "power") {
    sendCommand(unitId, "power", el.dataset.value === "true");
  }
}

let lastView = null;
let histAutoLoaded = false;
const _renderView = renderView;
renderView = function (view) {
  lastView = view;
  _renderView(view);
  populateUnits(view.units || []);
  if (!histAutoLoaded && view.units && view.units.length) {
    histAutoLoaded = true;
    loadHistory();
  }
};

$cards.addEventListener("click", onControl);
$cards.addEventListener("change", onControl);
$histUnit.addEventListener("change", loadHistory);
$histRange.addEventListener("change", loadHistory);
$histSite.addEventListener("change", loadHistory);
let histResizeTimer = null;
window.addEventListener("resize", () => {
  if (histResizeTimer) clearTimeout(histResizeTimer);
  histResizeTimer = setTimeout(loadHistory, 200);
});
refresh();
loadIdentity();
setInterval(refresh, POLL_MS);
setInterval(loadHistory, 60000);
setInterval(loadIdentity, 60000);
</script>
</body>
</html>
"""


def render_page(demo: bool) -> str:
    if not demo:
        return _PAGE
    return _PAGE.replace('<body data-demo="0">', '<body data-demo="1">')


# --- HTTP layer -----------------------------------------------------------------

class _PumpAppHandler(BaseHTTPRequestHandler):
    server_version = "villa-kuppis"

    @property
    def app(self) -> "PumpAppServer":
        return cast("PumpAppServer", self.server)

    def log_message(self, format, *args):  # no per-request stderr noise
        pass

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, obj) -> None:
        self._send(
            status,
            json.dumps(obj, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def _identity(self) -> dict | None:
        """Resolve the trusted identity of this connection.

        Returns ``{"actor", "display_name", "source", "login"}`` or None
        after an error response has already been sent.

        Without a configured public origin the only accepted host is the
        loopback, acting as the local actor; Tailscale identity headers
        are ignored. With a configured public origin the loopback hosts
        keep working as the local actor (the deployed runtime
        administers the app from the local PC while phones come through
        Serve), and non-loopback requests must carry the exact public
        authority, exactly one non-empty Tailscale-User-Login header on
        every route (identity-less requests are refused), and an Origin
        header, when present, that matches the configured origin
        exactly. Duplicate Host/Origin/identity headers are refused.
        Forwarded/X-Forwarded-* headers are never consulted for
        identity; Serve itself strips client-supplied identity headers
        before proxying to this loopback server.
        """
        port = self.app.server_port
        host_values = self.headers.get_all("Host")
        if not host_values or len(host_values) != 1:
            self._json(403, {"error": "forbidden",
                             "detail": "exactly one Host header is required"})
            return None
        host = host_values[0].strip().lower()
        if host in (f"127.0.0.1:{port}", f"localhost:{port}"):
            # Loopback is the local machine, trusted in every mode:
            # same-origin HTTP, Tailscale identity headers ignored.
            origins = self.headers.get_all("Origin")
            if origins is not None:
                if len(origins) != 1:
                    self._json(403, {"error": "forbidden",
                                     "detail": "duplicate Origin header"})
                    return None
                if origins[0].strip().lower() != "http://" + host:
                    self._json(403, {"error": "forbidden",
                                     "detail": "same origin required"})
                    return None
            return {"actor": self.app.local_actor, "display_name": None,
                    "source": "local", "login": None}
        if self.app.public_origin is None:
            self._json(403, {"error": "forbidden",
                             "detail": "local host required"})
            return None
        if host != self.app.public_authority:
            self._json(403, {"error": "forbidden",
                             "detail": "the host does not match the "
                                       "configured public origin"})
            return None
        origins = self.headers.get_all("Origin")
        if origins is not None:
            if len(origins) != 1:
                self._json(403, {"error": "forbidden",
                                 "detail": "duplicate Origin header"})
                return None
            if origins[0].strip().lower() != self.app.public_origin:
                self._json(403, {"error": "forbidden",
                                 "detail": "the origin does not match the "
                                           "configured public origin"})
                return None
        logins = self.headers.get_all("Tailscale-User-Login")
        if not logins:
            self._json(403, {"error": "forbidden",
                             "detail": "the Tailscale user login header "
                                       "is missing"})
            return None
        if len(logins) != 1:
            self._json(403, {"error": "forbidden",
                             "detail": "duplicate Tailscale user login "
                                       "header"})
            return None
        login = logins[0].strip()
        if not login:
            self._json(403, {"error": "forbidden",
                             "detail": "the Tailscale user login header "
                                       "is empty"})
            return None
        if _has_control_chars(login):
            self._json(403, {"error": "forbidden",
                             "detail": "the Tailscale user login header "
                                       "contains invalid characters"})
            return None
        if len(login) > MAX_ACTOR_LENGTH:
            self._json(403, {"error": "forbidden",
                             "detail": "the Tailscale user login header "
                                       f"is longer than {MAX_ACTOR_LENGTH} "
                                       "characters"})
            return None
        display = None
        names = self.headers.get_all("Tailscale-User-Name")
        if names:
            if len(names) != 1:
                self._json(403, {"error": "forbidden",
                                 "detail": "duplicate Tailscale user name "
                                           "header"})
                return None
            display = "".join(c for c in _decode_rfc2047(names[0]).strip()
                              if ord(c) >= 0x20 and ord(c) != 0x7F)
            if not display:
                display = None
        # The login is the stable identity; the display name is mutable
        # and not unique. Show the human-readable combined form while it
        # fits the event log column, otherwise the login alone.
        if display:
            combined = f"{display} ({login})"
            actor = combined if len(combined) <= MAX_ACTOR_LENGTH else login
        else:
            actor = login
        return {"actor": actor, "display_name": display,
                "source": "tailscale", "login": login}

    def do_GET(self) -> None:
        identity = self._identity()
        if identity is None:
            return
        parts = urlsplit(self.path)
        if parts.path == "/":
            self._send(200, self.app.page_html.encode("utf-8"),
                       "text/html; charset=utf-8")
        elif parts.path == "/api/view":
            self._json(200, self.app.service.view())
        elif parts.path == "/api/events":
            limit = 100
            qs = parse_qs(parts.query)
            if "limit" in qs:
                try:
                    limit = max(1, min(500, int(qs["limit"][0])))
                except ValueError:
                    pass
            self._json(200, self.app.service.events(limit))
        elif parts.path == "/api/history":
            qs = parse_qs(parts.query)
            unit = qs.get("unit", [None])[0]
            range_name = qs.get("range", ["day"])[0] or "day"
            site = qs.get("site", [None])[0]
            if not unit or not isinstance(unit, str):
                self._json(400, {"error": "bad_query",
                                 "detail": "unit is required"})
                return
            try:
                payload = self.app.service.history(unit, range_name, site)
            except ValueError as err:
                self._json(400, {"error": "bad_query", "detail": str(err)})
                return
            self._json(200, payload)
        elif parts.path == "/api/identity":
            self._json(200, {"actor": identity["actor"],
                             "display_name": identity["display_name"],
                             "source": identity["source"]})
        elif parts.path == "/api/command":
            self._json(405, {"error": "method_not_allowed",
                             "detail": "use POST for this path"})
        elif parts.path.startswith("/api/"):
            self._json(404, {"error": "not_found",
                             "detail": "unknown API path"})
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:
        identity = self._identity()
        if identity is None:
            return
        parts = urlsplit(self.path)
        if parts.path == "/api/command":
            self._post_command(identity)
        elif parts.path in ("/", "/api/view", "/api/events", "/api/history",
                            "/api/identity"):
            # Known paths with the wrong method: 405, mirroring how
            # do_GET answers /api/command.
            self._json(405, {"error": "method_not_allowed",
                             "detail": "use GET for this path"})
        elif parts.path.startswith("/api/"):
            self._json(404, {"error": "not_found",
                             "detail": "unknown API path"})
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    def _post_command(self, identity) -> None:
        if self.headers.get_content_type() != "application/json":
            self._json(415, {"error": "bad_body", "detail": "JSON body required"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY_BYTES:
            self._json(400, {"error": "bad_body",
                             "detail": "the request body is missing or too large"})
            return
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"error": "bad_body",
                             "detail": "the request body is not valid JSON"})
            return
        if not isinstance(body, dict):
            self._json(400, {"error": "bad_body",
                             "detail": "the request body must be a JSON object"})
            return
        unit = body.get("unit")
        action = body.get("action")
        value = body.get("value")
        if not isinstance(unit, str) or not isinstance(action, str):
            self._json(400, {"error": "bad_body",
                             "detail": "unit and action must be strings"})
            return
        # The actor is always the trusted identity of the connection
        # (the local actor or the Tailscale login); the body's user
        # field, if any, is ignored on purpose.
        status, payload = self.app.service.request(
            unit, action, value, identity["actor"])
        self._json(status, payload)


class PumpAppServer(ThreadingHTTPServer):
    """Localhost web app over a PumpService (demo or live)."""

    daemon_threads = True

    def __init__(self, service: PumpService, host: str = DEFAULT_HOST,
                 port: int = 0, demo: bool = False,
                 public_origin: str | None = None,
                 local_actor: str = DEFAULT_LOCAL_ACTOR):
        if host not in ("127.0.0.1", "localhost"):
            raise ValueError("the pump app must listen on localhost")
        if public_origin is not None:
            origin, authority = _validate_public_origin(public_origin)
        else:
            origin, authority = None, None
        actor = str(local_actor).strip()
        if not actor or len(actor) > MAX_ACTOR_LENGTH:
            raise ValueError("the local actor must be a non-empty string "
                             f"of at most {MAX_ACTOR_LENGTH} characters")
        self.service = service
        self.demo = demo
        self.public_origin = origin
        self.public_authority = authority
        self.local_actor = actor
        self.page_html = render_page(demo)
        super().__init__((host, port), _PumpAppHandler)

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}/"


# --- CLI --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="villa_kuppis.pump_app",
        description="Finnish pump web app: state, setpoint, mode, fan and "
                    "the event log for the home pumps.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("serve", help="run the web app")
    p.add_argument("--data-dir", required=True,
                   help="data directory holding pumps.db")
    p.add_argument("--host", default=DEFAULT_HOST,
                   help=f"bind address (default: {DEFAULT_HOST})")
    p.add_argument("--port", type=int, default=DEFAULT_PORT,
                   help=f"port (default: {DEFAULT_PORT}; 0 = pick one)")
    p.add_argument("--public-origin", default=None,
                   help="the HTTPS ts.net origin of Tailscale Serve; "
                        "enables trusted remote access and Tailscale user "
                        "identity, e.g. https://pump.tail1234.ts.net "
                        "(default: local access only)")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true",
                      help="real MELCloud Home (credentials from the "
                           "VILLA_KUPPIS_MELCLOUD_USERNAME / "
                           "VILLA_KUPPIS_MELCLOUD_PASSWORD environment "
                           "variables)")
    mode.add_argument("--demo", action="store_true",
                      help="fully offline demo with fake units")
    return parser


def _cmd_serve(args, print_fn=print) -> int:
    if args.host not in ("127.0.0.1", "localhost"):
        print_fn("error: the pump app must listen on localhost", file=sys.stderr)
        return 1
    if args.demo:
        service = make_demo_service(args.data_dir)
        mode = " (esittelytila: keksityt yksiköt)"
    else:
        try:
            service = make_live_service(args.data_dir)
        except RuntimeError as err:
            print_fn(f"error: {err}", file=sys.stderr)
            return 1
        mode = " (live: MELCloud Home)"
    server = None
    try:
        server = PumpAppServer(service, host=args.host, port=args.port,
                               demo=args.demo,
                               public_origin=args.public_origin)
        service.start()
        print_fn(f"Pumppusovellus käynnissä: {server.url}{mode} "
                 "(Ctrl+C pysäyttää)")
        server.serve_forever(poll_interval=0.5)
    except ValueError as err:
        print_fn(f"error: {err}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print_fn("Pysäytetty.")
    finally:
        if server is not None:
            server.server_close()
        service.close()
    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "serve":
        return _cmd_serve(args)
    build_parser().print_help()  # pragma: no cover (parser enforces)
    return 2  # pragma: no cover


if __name__ == "__main__":
    sys.exit(main())
