"""Finnish web app for the home pumps (card F2.3, front end).

Serves one self-contained page (inline CSS and JS, no external assets,
fully offline) plus a small JSON API on top of PumpService:

* GET  /              the Finnish app
* GET  /api/view      unit states, pending slots and service status
* GET  /api/events    the event log (newest first)
* POST /api/command   {"unit", "action", "value", "user"} -> the service

The page shows the cloud-reported state and the requested change
separately until the device confirms it (docs/ui-fi.md). Format is
fi-FI: decimal comma, Celsius, 24-hour clock, Europe/Helsinki times.

Offline-first: importing this module and ``--help`` never touch the
network. Demo mode runs the whole app against the in-memory FakeClient;
live mode uses the environment credentials of make_live_service
(development mode; F3.2 moves them to the Windows credential store).
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast
from urllib.parse import parse_qs, urlsplit

from .pump_probe import FakeClient, _unit_payload
from .pump_service import PumpService, make_live_service

DEFAULT_HOST = "127.0.0.1"
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
  <span id="service-status" role="status"><span class="badge info">Yhteyttä muodostetaan…</span></span>
  <span id="last-updated"></span>
</header>
<div id="demo-banner">Esittelytila: näytetyt arvot ovat keksittyjä ja toiminnot
ohjaavat vain esittelyyksiköitä.</div>
<main>
  <section id="cards" aria-label="Pumppulaitteet"></section>
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

let sending = false;
async function sendCommand(unitId, action, value) {
  if (sending) return;
  sending = true;
  try {
    const res = await fetch("/api/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ unit: unitId, action: action, value: value,
                             user: "" })
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
const _renderView = renderView;
renderView = function (view) { lastView = view; _renderView(view); };

$cards.addEventListener("click", onControl);
$cards.addEventListener("change", onControl);
refresh();
setInterval(refresh, POLL_MS);
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

    def do_GET(self) -> None:
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
        elif parts.path == "/api/command":
            self._json(405, {"error": "method_not_allowed",
                             "detail": "use POST for this path"})
        elif parts.path.startswith("/api/"):
            self._json(404, {"error": "not_found",
                             "detail": "unknown API path"})
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:
        parts = urlsplit(self.path)
        if parts.path == "/api/command":
            self._post_command()
        elif parts.path in ("/", "/api/view", "/api/events"):
            # Known paths with the wrong method: 405, mirroring how
            # do_GET answers /api/command.
            self._json(405, {"error": "method_not_allowed",
                             "detail": "use GET for this path"})
        elif parts.path.startswith("/api/"):
            self._json(404, {"error": "not_found",
                             "detail": "unknown API path"})
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    def _post_command(self) -> None:
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
        user = body.get("user")
        if not isinstance(unit, str) or not isinstance(action, str):
            self._json(400, {"error": "bad_body",
                             "detail": "unit and action must be strings"})
            return
        if isinstance(user, str):
            user = user.strip()[:80] or None
        elif user is not None:
            user = None
        status, payload = self.app.service.request(unit, action, value, user)
        self._json(status, payload)


class PumpAppServer(ThreadingHTTPServer):
    """Localhost web app over a PumpService (demo or live)."""

    daemon_threads = True

    def __init__(self, service: PumpService, host: str = DEFAULT_HOST,
                 port: int = 0, demo: bool = False):
        self.service = service
        self.demo = demo
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
    service.start()
    server = PumpAppServer(service, host=args.host, port=args.port,
                           demo=args.demo)
    print_fn(f"Pumppusovellus käynnissä: {server.url}{mode} "
             "(Ctrl+C pysäyttää)")
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print_fn("Pysäytetty.")
    finally:
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
