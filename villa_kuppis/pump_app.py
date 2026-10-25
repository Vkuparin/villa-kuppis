"""Finnish web app for the home pumps (card F2.3, front end).

Serves one self-contained page (inline CSS and JS, no external assets,
fully offline) plus a small JSON API on top of PumpService:

* GET  /              the Finnish app
* GET  /manifest.webmanifest, /sw.js, /icon*.png, /icon.svg  installable PWA (card H3.1)
* GET  /api/view      unit states, pending slots and service status
* GET  /api/events    the event log (newest first)
* GET  /api/history   downsampled time series (card F3.3)
* GET  /api/identity  the trusted actor of this connection (card F3.2)
* GET  /api/settings  setting definitions, overrides and effective values
* GET  /api/plan      today's cooling plan (card F4.1)
* GET  /api/rooms     learned room rates and the FMI forecast (card F5.1)
* GET  /api/cottage   cottage rooms, readings and connection (card H2.3, read-only)
* GET/POST /api/visit  cottage visit: state and plan; start {"arrival", "target", "rooms"} (card F4.3)
* POST /api/visit/end  end the active visit
* GET  /api/sites     connection health of each Home Assistant site
* POST /api/settings  {"scope", "key", "value" | "reset"} (card F3.5)
* GET/POST /api/away  home-away button, {"away": bool} (card F6.1)
* POST /api/prices/refresh  fetch today's and tomorrow's prices if missing
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
from datetime import datetime
from email.header import decode_header
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast
from urllib.parse import parse_qs, urlsplit

from .pump_probe import FakeClient, _unit_payload
from . import settings as app_settings
from . import cottage as cottage_ui
from .cottage import CottageSite, FakeSiteGateway
from .visit import VisitError
from .pump_service import MAX_ACTOR_LENGTH, PumpService, make_live_service
from .plan_ui import SCRIPT as _PLAN_SCRIPT
from .plan_ui import SECTION_HTML as _PLAN_HTML
from .plan_ui import STYLE as _PLAN_STYLE
from .rooms_ui import SCRIPT as _ROOMS_SCRIPT
from .rooms_ui import SECTION_HTML as _ROOMS_HTML
from .rooms_ui import STYLE as _ROOMS_STYLE
from .settings_ui import SCRIPT as _SETTINGS_SCRIPT
from .settings_ui import SECTION_HTML as _SETTINGS_HTML
from .settings_ui import STYLE as _SETTINGS_STYLE

DEFAULT_HOST = "127.0.0.1"
# Home Assistant app (Supervisor) mode: the ingress proxy always connects
# from this address and adds the Home Assistant user as X-Remote-User-*.
INGRESS_PROXY = "172.30.32.2"
LOOPBACK_PEERS = ("127.0.0.1", "::1")
# The actor used for every request that arrives without a trusted remote
# identity (i.e. directly on the loopback interface).
DEFAULT_LOCAL_ACTOR = "Paikallinen käyttäjä"
DEFAULT_PORT = 8377
MAX_BODY_BYTES = 64 * 1024
DEFAULT_PLAN_INTERVAL = 60.0

DEMO_UNITS = (
    _unit_payload("demo-1", "Demo-yksikkö 1", mode="Cool", power="True",
                  set_temp="23.5", room="26", fan="Auto"),
    _unit_payload("demo-2", "Demo-yksikkö 2", mode="Cool", power="False",
                  set_temp="22", room="27", fan="Two"),
    _unit_payload("demo-3", "Demo-yksikkö 3", mode="Fan", power="True",
                  set_temp="23", room="25", fan="One"),
)


def _demo_cottage(data_dir) -> CottageSite:
    gateway = FakeSiteGateway()
    return CottageSite(data_dir, gateway, gateway.bindings, sample_interval=5.0,
                       target_interval=5.0)


def make_demo_service(data_dir, *, poll_interval: float = 5.0,
                      stale_after: float = 60.0,
                      readback_polls: int = 2, readback_delay: float = 1.0,
                      command_timeout: float = 10.0,
                      automation_interval: float | None = None,
                      automation_default_on: bool = True) -> PumpService:
    """Offline service: the real pipeline against the in-memory FakeClient."""
    client = FakeClient([dict(p) for p in DEMO_UNITS])
    return PumpService(
        data_dir, client=client, poll_interval=poll_interval,
        stale_after=stale_after, readback_polls=readback_polls,
        readback_delay=readback_delay, command_timeout=command_timeout,
        automation_interval=automation_interval,
        automation_default_on=automation_default_on,
        price_fetch_enabled=False,
        cottage_factory=_demo_cottage,
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


# --- PWA files (card H3.1) ----------------------------------------------------

_STATIC_DIR = Path(__file__).parent / "static"
# path -> (file, content type, Cache-Control). The service worker and the
# manifest are always revalidated so an update reaches the phone quickly.
_STATIC_FILES = {
    "/manifest.webmanifest": ("manifest.webmanifest",
                              "application/manifest+json; charset=utf-8", "no-cache"),
    "/sw.js": ("sw.js", "text/javascript; charset=utf-8", "no-cache"),
    "/icon.svg": ("icon.svg", "image/svg+xml", "public, max-age=86400"),
    "/icon-192.png": ("icon-192.png", "image/png", "public, max-age=86400"),
    "/icon-512.png": ("icon-512.png", "image/png", "public, max-age=86400"),
    "/icon-maskable-512.png": ("icon-maskable-512.png", "image/png",
                               "public, max-age=86400"),
}


# --- the page ---------------------------------------------------------------

_PAGE = """<!doctype html>
<html lang="fi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pumput</title>
<link rel="manifest" href="manifest.webmanifest">
<meta name="theme-color" content="#0b5fff">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Kuppis">
<link rel="icon" href="icon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="icon-192.png">
<script>
(function () {
  var t = null, a = null;
  try { t = localStorage.getItem("vk-theme"); a = localStorage.getItem("vk-accent"); } catch (e) {}
  if (t !== "dark" && t !== "light")
    t = window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", t);
  if (a) document.documentElement.setAttribute("data-accent", a);
})();
</script>
<style>
/* Themes: light by default, dark by choice or the device setting. The
   accent comes from data-accent. Both are set before first paint. */
:root {
  color-scheme: light;
  --bg: #f2f4f6; --surface: #fff; --surface2: #eef1f4;
  --text: #1a1d21; --text2: #44505c; --text3: #55606b;
  --line: #d9dee3; --line2: #e3e8ec; --line3: #c7cfd6; --ctl: #9aa4ae;
  --ok: #0b6b2a; --ok-bg: #e8f6ec;
  --warn: #8a5b00; --warn-bg: #fdf3dd; --warn-line: #d9b96a;
  --warn-card: #fffdf6; --warn-text: #6b4d00;
  --err: #a11d1d; --err-bg: #fdecec; --err-text: #7a1414;
  --info: #0b3f9e; --info-bg: #eef4ff; --purple: #6a2c91;
  --accent: #0b5fff;
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --bg: #12151a; --surface: #1c2128; --surface2: #262d36;
  --text: #e6e9ed; --text2: #b4bec9; --text3: #99a4b0;
  --line: #333b46; --line2: #2b323b; --line3: #46505c; --ctl: #6b7683;
  --ok: #5fd28a; --ok-bg: #14301f;
  --warn: #f0c060; --warn-bg: #33280e; --warn-line: #7a6330;
  --warn-card: #2a2616; --warn-text: #f0d58a;
  --err: #ff8a8a; --err-bg: #3a1717; --err-text: #ffc2c2;
  --info: #7fb0ff; --info-bg: #17283f; --purple: #c79bf0;
  --accent: #4d8dff;
}
:root[data-accent="green"] { --accent: #0b8a3a; }
:root[data-accent="purple"] { --accent: #7a3fc0; }
:root[data-accent="pink"] { --accent: #d6336c; }
:root[data-accent="orange"] { --accent: #d9640b; }
:root[data-theme="dark"][data-accent="green"] { --accent: #3fc46e; }
:root[data-theme="dark"][data-accent="purple"] { --accent: #b389f0; }
:root[data-theme="dark"][data-accent="pink"] { --accent: #f2709f; }
:root[data-theme="dark"][data-accent="orange"] { --accent: #f09040; }
header.top { border-bottom: 3px solid var(--accent) !important; }
header.top .auto-switch { margin-left: auto; align-self: center; }
button.auto-switch[hidden] { display: none; }
h1 { color: var(--accent); }
* { box-sizing: border-box; }
body { margin: 0; font: 16px/1.45 system-ui, "Segoe UI", sans-serif;
       background: var(--bg); color: var(--text); }
header.top { display: flex; flex-wrap: wrap; gap: .5rem 1.25rem;
             align-items: baseline; padding: .9rem 1.25rem; background: var(--surface);
             border-bottom: 1px solid var(--line); }
h1 { font-size: 1.35rem; margin: 0; }
.badge { display: inline-block; padding: .18rem .6rem; border-radius: .5rem;
         border: 1px solid var(--ctl); font-size: .85rem; }
.badge.ok   { color: var(--ok); border-color: var(--ok); background: var(--ok-bg); }
.badge.warn { color: var(--warn); border-color: var(--warn); background: var(--warn-bg); }
.badge.err  { color: var(--err); border-color: var(--err); background: var(--err-bg); }
.badge.info { color: var(--info); border-color: var(--info); background: var(--info-bg); }
#identity { max-width: 240px; overflow: hidden; text-overflow: ellipsis;
           white-space: nowrap; font-size: .85rem; color: var(--text2);
           background: var(--surface2); border: 1px solid var(--line);
           border-radius: .5rem; padding: .18rem .6rem; }
#last-updated { font-size: .85rem; color: var(--text3); }
.health { display: inline-flex; flex-wrap: wrap; gap: .4rem; }
.health button.badge { min-height: 36px; min-width: 0; cursor: pointer; font: inherit;
                       font-size: .85rem; }
.health button.badge[hidden] { display: none; }
.badge.mute { color: var(--text3); border-color: var(--ctl); background: var(--surface2); }
.health-detail, .offline-banner { padding: .55rem 1.25rem; font-size: .92rem;
                                  border-bottom: 1px solid var(--line); }
.health-detail { background: var(--surface2); color: var(--text2); }
.offline-banner { background: var(--warn-bg); border-color: var(--warn-line);
                  color: var(--warn-text); }
.health-detail[hidden], .offline-banner[hidden] { display: none; }
body[data-conn="down"] #cards, body[data-conn="down"] .cottage-rooms { opacity: .55; }
#demo-banner { display: none; padding: .6rem 1.25rem; background: var(--warn-bg);
               border-bottom: 1px solid var(--warn-line); color: var(--warn-text); }
body[data-demo="1"] #demo-banner { display: block; }
main { padding: 1.25rem; max-width: 1100px; margin: 0 auto; }
#cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(290px, 1fr));
         gap: 1rem; margin-bottom: 2rem; }
.card { background: var(--surface); border: 1px solid var(--line); border-radius: .75rem;
        padding: 1rem 1.1rem; }
.card.stale { border-color: var(--warn-line); background: var(--warn-card); }
.card-head { display: flex; justify-content: space-between; align-items: baseline;
             gap: .75rem; flex-wrap: wrap; margin-bottom: .5rem; }
.card-head h3 { margin: 0; font-size: 1.1rem; }
.row { display: flex; justify-content: space-between; align-items: center;
       gap: .6rem; padding: .3rem 0; flex-wrap: wrap; }
.row .label { color: var(--text2); }
.value { font-variant-numeric: tabular-nums; }
.value.big { font-size: 1.3rem; font-weight: 600; }
button, select { min-height: 44px; min-width: 44px; font-size: 1rem;
                 border: 1px solid var(--ctl); border-radius: .5rem;
                 background: var(--surface); color: var(--text); padding: 0 .7rem;
                 cursor: pointer; }
select { cursor: pointer; min-width: 110px; }
button:disabled, select:disabled { opacity: .55; cursor: not-allowed; }
button:focus-visible, select:focus-visible, a:focus-visible {
  outline: 3px solid var(--accent); outline-offset: 2px; }
button.switch { display: inline-flex; align-items: center; gap: .6rem;
                border: 0; background: none; padding: 0; min-width: 0; }
button.switch .track { position: relative; width: 52px; height: 30px;
                       border-radius: 15px; background: var(--ctl);
                       transition: background .15s; flex: none; }
button.switch .track::after { content: ""; position: absolute; top: 3px; left: 3px;
                              width: 24px; height: 24px; border-radius: 50%;
                              background: #fff; transition: transform .15s;
                              box-shadow: 0 1px 3px rgba(0,0,0,.35); }
button.switch[aria-checked="true"] .track { background: var(--accent); }
button.switch[aria-checked="true"] .track::after { transform: translateX(22px); }
.stepper { display: inline-flex; gap: .4rem; margin-left: auto; }
.stepper button { min-width: 44px; font-size: 1.15rem; font-weight: 700; }
.pending { margin-top: .8rem; padding: .55rem .75rem; border: 1px dashed var(--accent);
           border-radius: .5rem; background: var(--info-bg); color: var(--info); }
section.events { background: var(--surface); border: 1px solid var(--line);
                 border-radius: .75rem; padding: 1rem 1.1rem; }
section.events h2 { margin: 0 0 .6rem; font-size: 1.15rem; }
section.history { background: var(--surface); border: 1px solid var(--line);
                  border-radius: .75rem; padding: 1rem 1.1rem;
                  margin-bottom: 1rem; }
section.history h2 { margin: 0 0 .6rem; font-size: 1.15rem; }
.hist-controls { display: flex; flex-wrap: wrap; gap: .6rem 1.25rem;
                 align-items: end; margin-bottom: .75rem; }
.hist-controls label { display: flex; flex-direction: column; gap: .2rem;
                       font-size: .85rem; color: var(--text2); }
#hist-status { font-size: .85rem; color: var(--text3); }
.hist-chart { width: 100%; position: relative; }
.hist-chart svg { touch-action: pan-y; }
.hist-tip { position: absolute; top: 8px; pointer-events: none; z-index: 5;
            background: var(--surface); color: var(--text); border: 1px solid var(--line3);
            border-radius: .5rem; padding: .35rem .6rem; font-size: .85rem;
            box-shadow: 0 2px 8px rgba(0,0,0,.25); white-space: nowrap; }
.hist-tip b { display: block; margin-bottom: .15rem; font-weight: 600; }
.hist-tip i { display: inline-block; width: .9rem; height: .25rem; margin-right: .4rem;
              vertical-align: middle; }
.hist-chart svg { display: block; width: 100%; height: auto; }
.hist-legend { list-style: none; display: flex; flex-wrap: wrap;
               gap: .4rem 1.1rem; padding: .4rem 0 0; margin: 0;
               font-size: .85rem; color: var(--text2); }
.hist-legend .swatch { display: inline-block; width: 1.4rem; height: .28rem;
                       margin-right: .35rem; vertical-align: middle; }
.hist-energy { margin-top: .75rem; font-size: .9rem; }
.hist-energy table { max-width: 32rem; }
.hist-energy th, .hist-energy td { text-align: right; padding: .15rem .5rem; }
.hist-energy th:first-child, .hist-energy td:first-child { text-align: left; }
.hist-empty { color: var(--text3); padding: 1rem 0; }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; padding: .45rem .6rem; border-bottom: 1px solid var(--line2);
         vertical-align: top; }
th { color: var(--text2); font-weight: 600; }
td.result-confirmed { color: var(--ok); }
td.result-failed { color: var(--err); }
td.result-pending { color: var(--info); }
.detail { display: block; font-size: .85rem; color: var(--text3); }
#no-events { color: var(--text3); }
#alerts { position: fixed; left: 1rem; right: 1rem; bottom: 1rem;
          display: flex; flex-direction: column; gap: .5rem; z-index: 10; }
.alert { background: var(--err-bg); border: 1px solid var(--err); color: var(--err-text);
         padding: .6rem .9rem; border-radius: .5rem; }
.alert.info { background: var(--info-bg); border-color: var(--info); color: var(--info); }
/*SETTINGS_STYLE*/
/*PLAN_STYLE*/
/*ROOMS_STYLE*/
/*COTTAGE_STYLE*/
td.result-observed { color: var(--text2); }
@media (max-width: 560px) {
  .row { flex-direction: column; align-items: stretch; gap: .35rem; }
  .row .label { order: 1; }
  .row > *:not(.label) { order: 2; }
  th, td { padding: .4rem .45rem; font-size: .92rem; }
  /* Event log: one card per event instead of a five-column table. */
  table.ev, table.ev tbody, table.ev tr, table.ev td { display: block; width: 100%; }
  table.ev thead { display: none; }
  table.ev tr { border: 1px solid var(--line); border-radius: .6rem;
                padding: .35rem .7rem; margin-bottom: .6rem; }
  table.ev td { display: flex; justify-content: space-between; gap: 1rem;
                border: 0; padding: .15rem 0; text-align: right; }
  table.ev td::before { content: attr(data-label); color: var(--text3);
                        text-align: left; flex: none; }
  table.ev td#no-events { justify-content: flex-start; }
  table.ev td#no-events::before { content: none; }
}
/* Control text sits on the right, next to the switches. */
.set-row input, .set-row select, .card select { text-align: right; text-align-last: right; }
</style>
</head>
<body data-demo="0">
<header class="top">
  <h1>Pumput</h1>
  <span id="identity" class="identity" title="Kirjautunut käyttäjä"></span>
  <span id="health" class="health" role="status" aria-label="Kohteiden tila">
    <button type="button" class="badge info" id="health-home" data-site="home">Koti: yhdistetään…</button>
    <button type="button" class="badge info" id="health-cottage" data-site="cottage" hidden>Mökki: yhdistetään…</button>
  </span>
  <span id="last-updated"></span>
  <button type="button" class="switch auto-switch" role="switch" aria-checked="false"
          id="auto-btn" title="Hinta- ja lämpötilaohjattu automaatio. Pois päältä se ei lähetä pumpuille komentoja; pumput jäävät siihen tilaan, jossa ne ovat."><span class="track"></span><span>Automaatio</span></button>
</header>
<div id="health-detail" class="health-detail" role="status" hidden></div>
<div id="offline-banner" class="offline-banner" role="status" hidden></div>
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
      <span class="hist-nav" role="group" aria-label="Siirry ajassa">
        <button type="button" id="hist-prev" aria-label="Aiemmin">&larr;</button>
        <button type="button" id="hist-now">Nyt</button>
        <button type="button" id="hist-next" aria-label="Myöhemmin">&rarr;</button>
      </span>
      <span id="hist-status" role="status"></span>
    </div>
    <div id="hist-chart" class="hist-chart" role="img"
         aria-label="Lämpötilojen ja sähkönhinnan kaavio"></div>
    <ul id="hist-legend" class="hist-legend"></ul>
    <div id="hist-energy" class="hist-energy"></div>
    <p class="detail">Ulkolämpötila: Ilmatieteen laitoksen havaintoasema,
      <a href="https://en.ilmatieteenlaitos.fi/open-data-licence">avoin data (CC BY 4.0)</a>.
      Sähkönhinta: Elering. Käyrät on harvennettu näytölle.</p>
  </section>
<!--COTTAGE_SECTION-->
<!--PLAN_SECTION-->
<!--ROOMS_SECTION-->
<!--SETTINGS_SECTION-->
  <section class="events" aria-label="Tapahtumat">
    <h2>Tapahtumat</h2>
    <table class="ev">
      <thead>
        <tr><th>Milloin</th><th>Yksikkö</th><th>Kuka</th><th>Muutos</th><th>Tulos</th></tr>
      </thead>
      <tbody id="event-rows"><tr><td colspan="5" id="no-events">Ei tapahtumia.</td></tr></tbody>
    </table>
  </section>
</main>
<div id="alerts" role="alert" aria-live="assertive"></div>
<script>/*HEALTH_SCRIPT*/</script>
<script>
"use strict";
const MODE_NAMES = { Cool: "Jäähdytys", Heat: "Lämmitys", Dry: "Kuivaus",
                     Fan: "Puhallus", Automatic: "Automaattinen",
                     follow: "Seuraa anturia", thermostat: "Patterien termostaatti",
                     fallback: "Turvalämpö", manual: "Käsiohjaus" };
const FAN_NAMES = { Auto: "Automaattinen", One: "1", Two: "2", Three: "3",
                    Four: "4", Five: "5" };
const MODES = ["Cool", "Heat", "Dry", "Fan", "Automatic"];
const FAN_SPEEDS = ["Auto", "One", "Two", "Three", "Four", "Five"];
const RESULT_TEXT = { pending: "Lähetetään…", confirmed: "Vahvistettu",
                      failed: "Muutos epäonnistui", observed: "Havaittu" };
const POLL_MS = 5000;

const $cards = document.getElementById("cards");
const $rows = document.getElementById("event-rows");
const $alerts = document.getElementById("alerts");
const $histUnit = document.getElementById("hist-unit");
const $histRange = document.getElementById("hist-range");
const $histSite = document.getElementById("hist-site");
const $histPrev = document.getElementById("hist-prev");
const $histNext = document.getElementById("hist-next");
const $histNow = document.getElementById("hist-now");
// End of the shown window in ms; null follows the present. The server lets
// it run up to 48 h ahead (stored prices and the weather forecast).
let histEnd = null;
let histNowMs = Date.now(), histMaxEnd = Date.now();
const RANGE_MS = { day: 864e5, week: 6048e5, month: 2592e6, year: 31536e6 };
const $histChart = document.getElementById("hist-chart");
const $histStatus = document.getElementById("hist-status");
const $histLegend = document.getElementById("hist-legend");
const $histEnergy = document.getElementById("hist-energy");

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
    case "target": return "Mökin tavoite → " + fmtTemp(parseFloat(requested));
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
function setpointRange(unit) {
  if (unit.mode === null || unit.mode === "Fan") return null;
  const caps = unit.caps;
  let lo = null, hi = null;
  if (caps) {
    if (unit.mode === "Heat") { lo = caps.min_heat; hi = caps.max_heat; }
    else if (unit.mode === "Cool" || unit.mode === "Dry") {
      lo = caps.min_cool; hi = caps.max_cool; }
    else if (unit.mode === "Automatic") { lo = caps.min_auto; hi = caps.max_auto; }
  }
  if (lo === null || hi === null || unit.setpoint === null) return null;
  return [lo, hi];
}
function setpointAllowed(unit) {
  return setpointRange(unit) !== null;
}
function setpointOptions(unit, step) {
  const range = setpointRange(unit);
  if (!range) return "";
  const values = [];
  for (let v = range[0]; v <= range[1] + 1e-9; v += step) values.push(v);
  if (!values.includes(unit.setpoint)) values.push(unit.setpoint);
  values.sort((a, b) => a - b);
  return values.map((v) =>
    '<option value="' + v + '"' + (v === unit.setpoint ? " selected" : "") + ">" +
    esc(fmtTemp(v)) + "</option>").join("");
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
    '<button class="switch" role="switch" aria-checked="' + (unit.power === true) +
    '" data-action="power" data-unit="' + esc(unit.id) + '" data-value="' +
    (unit.power === true ? "false" : "true") + '"' + dis + ">" +
    '<span class="track"></span><span>' + esc(powerLabel) + "</span></button></div>"
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
    '<select data-action="setpoint-select" data-unit="' + esc(unit.id) +
    '" aria-label="Valitse tavoitelämpötila"' + (canStep ? "" : " disabled") + ">" +
    setpointOptions(unit, step) + "</select>" +
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
    ? '<div class="pending"><b>Odottaa vahvistusta:</b> ' +
      esc(changeText(pending.action, pending.requested)) +
      " &mdash; uusi arvo näytetään vasta, kun laite ilmoittaa sen</div>"
    : "";
  return (
    '<article class="card' + (unit.fresh ? "" : " stale") + '">' +
    '<header class="card-head"><h3>' + esc(unit.name) + "</h3>" +
    (unit.fresh ? "" :
      '<span class="badge warn" title="Laitteelta ei ole saatu tuoretta lukemaa ' +
      'vähään aikaan, joten näytetyt arvot voivat olla vanhoja.">Tiedot vanhentuneet</span>') +
    "</header>" +
    rows.join("") + pendingHtml + "</article>"
  );
}

let selectHoldUntil = 0;
let lastCardsHtml = null;
function renderView(view) {
  const svc = view.service;
  vkHealth.viewOk(view);
  const text = vkHealth.homeText(svc.state);
  document.title = svc.state === "ok" ? "Pumput" : "Pumput — " + text;
  // Rebuilding the cards would close a list the user is choosing from. Some
  // browsers (Android, HA's iframe) blur the select while its list is open, so
  // a recent touch of a select also holds the rebuild back.
  const active = document.activeElement;
  if (active && active.tagName === "SELECT" && $cards.contains(active)) return;
  if (Date.now() < selectHoldUntil) return;
  const html = view.units.map(cardHtml).join("") ||
    '<p class="badge info">Ei yksiköitä tiedossa vielä.</p>';
  if (html !== lastCardsHtml) {
    $cards.innerHTML = html;
    lastCardsHtml = html;
  }
}

function renderEvents(events) {
  if (!events.length) {
    $rows.innerHTML = '<tr><td colspan="5" id="no-events">Ei tapahtumia.</td></tr>';
    return;
  }
  $rows.innerHTML = events.map((ev) => (
    "<tr>" +
    '<td data-label="Milloin" title="' + esc(fmtClock(ev.ts)) + '">' + esc(fmtWhen(ev.ts)) + "</td>" +
    '<td data-label="Yksikkö">' + esc(ev.unit_name) + "</td>" +
    '<td data-label="Kuka">' + esc(ev.actor) + "</td>" +
    '<td data-label="Muutos">' + esc(changeText(ev.action, ev.requested)) + "</td>" +
    '<td data-label="Tulos" class="result-' + esc(ev.result) + '"><span>' +
    esc(RESULT_TEXT[ev.result] || ev.result) +
    (ev.detail ? '<span class="detail">' + esc(ev.detail) + "</span>" : "") +
    "</span></td></tr>"
  )).join("");
}

// --- history graphs (card F3.3): time series, gaps kept as gaps ---
const RANGE_MAX_POINTS = { day: 288, week: 700, month: 900, year: 1000 };
const HIST_SERIES = [
  { key: "room_temp", label: "Huonelämpötila", color: "var(--ok)",
    scale: "temp", dash: "" },
  { key: "setpoint", label: "Tavoitelämpötila", color: "var(--err)",
    scale: "temp", dash: "7 5" },
  { key: "outdoor", label: "Ulkolämpötila", color: "var(--info)",
    scale: "temp", dash: "2 4" },
  { key: "plan", label: "Suunnitelman tavoite", color: "var(--purple)",
    scale: "temp", dash: "10 3 2 3" },
  { key: "forecast", label: "Sääennuste", color: "var(--info)",
    scale: "temp", dash: "8 3" },
];
// Cottage heaters and similar: the server sends them as data.extra.
const EXTRA_COLORS = ["var(--accent)", "var(--purple)", "var(--warn)"];
const PRICE_SERIES = { key: "price", label: "Sähkön hinta (snt/kWh)", vat: null,
                       color: "var(--warn)", scale: "price", dash: "1 4" };

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
  const labels = data.labels || {};
  const series = HIST_SERIES
    .map((s) => Object.assign({}, s, {
      label: labels[s.key] || s.label,
      pts: (data[s.key] || []).map(([iso, v]) => [new Date(iso).getTime(), v])
    }))
    .concat((data.extra || []).map((e, i) => ({
      key: e.key, label: e.label, color: EXTRA_COLORS[i % EXTRA_COLORS.length],
      scale: "temp", dash: "6 3",
      pts: e.points.map(([iso, v]) => [new Date(iso).getTime(), v])
    })))
    .filter((s) => s.pts.length > 0);
  const pricePts = (data.price || [])
    .map(([iso, v]) => [new Date(iso).getTime(), v]);
  const allTemp = series.flatMap((s) => s.pts);
  if (!allTemp.length && !pricePts.length) {
    $histChart.innerHTML = '<p class="hist-empty">Ei historiaa tälle valinnalle.</p>';
    $histLegend.textContent = "";
    $histEnergy.textContent = "";
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
      stroke: "var(--line2)", "stroke-width": 1 }));
    svg.appendChild(svgEl("text", { x: m.left - 6, y: y + 4, "text-anchor": "end",
      "font-size": 11, fill: "var(--text2)" }, tempFmt.format(v) + "\u00b0"));
  }
  if (hasPrice) {
    for (const v of niceTicks(pMin, pMax, 5)) {
      const y = YP(v);
      svg.appendChild(svgEl("text", { x: W - m.right + 6, y: y + 4,
        "text-anchor": "start", "font-size": 11, fill: "var(--warn)" },
        tempFmt.format(v) + "s"));
    }
  }
  const nTime = Math.max(2, Math.min(8, Math.floor((W - m.left - m.right) / 90)));
  for (let i = 0; i <= nTime; i++) {
    const t = t0 + (i / nTime) * (t1 - t0);
    const x = X(t);
    svg.appendChild(svgEl("line", { x1: x, x2: x, y1: m.top, y2: H - m.bottom,
      stroke: "var(--surface2)", "stroke-width": 1 }));
    svg.appendChild(svgEl("text", { x: x, y: H - m.bottom + 16,
      "text-anchor": "middle", "font-size": 11, fill: "var(--text2)" },
      fmtTimeTick(t, data.range)));
  }
  svg.appendChild(svgEl("rect", { x: m.left, y: m.top,
    width: W - m.left - m.right, height: H - m.top - m.bottom,
    fill: "none", stroke: "var(--line3)" }));

  const tNow = data.now ? new Date(data.now).getTime() : null;
  if (tNow !== null && tNow > t0 && tNow < t1) {
    svg.insertBefore(svgEl("rect", { x: X(tNow), y: m.top,
      width: W - m.right - X(tNow), height: H - m.top - m.bottom,
      fill: "var(--surface2)", opacity: 0.55 }), svg.children[0]);
    svg.appendChild(svgEl("line", { x1: X(tNow), x2: X(tNow), y1: m.top,
      y2: H - m.bottom, stroke: "var(--text3)", "stroke-width": 1 }));
    svg.appendChild(svgEl("text", { x: X(tNow) + 4, y: m.top + 11,
      "font-size": 11, fill: "var(--text3)" }, "nyt"));
  }

  const expected = (t1 - t0) / maxPts;
  const gapFor = (sampleMs) => Math.max(3 * sampleMs, 2 * expected);

  for (const s of series) {
    const d = seriesPath(s.pts, gapFor(s.key === "outdoor" || s.key === "forecast" ? 3600000 : 300000), X, YT);
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

  // Confirmed or failed commands: a tick along the bottom edge with a tooltip.
  const marks = data.commands || [];
  for (const c of marks) {
    const t = new Date(c.ts).getTime();
    if (!(t >= t0 && t <= t1)) continue;
    const x = X(t);
    const color = c.result === "failed" ? "var(--err)" : "var(--text2)";
    const tick = svgEl("line", { x1: x, x2: x, y1: H - m.bottom - 12,
      y2: H - m.bottom, stroke: color, "stroke-width": 2 });
    tick.appendChild(svgEl("title", {}, fmtTimeTick(t, "week") + " " +
      c.actor + ": " + changeText(c.action, c.requested) + " (" +
      (RESULT_TEXT[c.result] || c.result) + ")"));
    svg.appendChild(tick);
  }

  $histChart.appendChild(svg);

  // Hover or touch: the values nearest to the pointer's time.
  const priceFmt = new Intl.NumberFormat("fi-FI", { maximumFractionDigits: 2 });
  const guide = svgEl("line", { y1: m.top, y2: H - m.bottom, stroke: "var(--text3)",
    "stroke-width": 1, "stroke-dasharray": "3 3", visibility: "hidden" });
  svg.appendChild(guide);
  const tip = document.createElement("div");
  tip.className = "hist-tip";
  tip.hidden = true;
  $histChart.appendChild(tip);
  const nearest = (pts, t, tol) => {
    let best = null, bestD = Infinity;
    for (const p of pts) {
      if (p[1] === null || p[1] === undefined) continue;
      const d = Math.abs(p[0] - t);
      if (d < bestD) { bestD = d; best = p; }
    }
    return best && bestD <= tol ? best : null;
  };
  const showTip = (ev) => {
    const rect = svg.getBoundingClientRect();
    const px = (ev.clientX - rect.left) * (W / rect.width);
    if (px < m.left || px > W - m.right) { hideTip(); return; }
    const t = t0 + (px - m.left) / (W - m.left - m.right) * (t1 - t0);
    const rows = [];
    for (const s of series) {
      const sample = s.key === "outdoor" || s.key === "forecast" ? 3600000 : 300000;
      const hit = nearest(s.pts, t, Math.max(sample, expected) * 1.5);
      if (hit) rows.push('<div><i style="background:' + s.color + '"></i>' +
        esc(s.label) + ": " + esc(tempFmt.format(hit[1])) + " \u00b0C</div>");
    }
    if (hasPrice) {
      const hit = nearest(pricePts, t, Math.max(900000, expected) * 1.5);
      if (hit) rows.push('<div><i style="background:' + PRICE_SERIES.color + '"></i>' +
        "Sähkön hinta: " + esc(priceFmt.format(hit[1])) + " snt/kWh" +
        (PRICE_SERIES.vat == null ? "" : " (sis. ALV)") + "</div>");
    }
    const stamp = new Intl.DateTimeFormat("fi-FI", { timeZone: "Europe/Helsinki",
      weekday: "short", day: "numeric", month: "numeric", hour: "2-digit",
      minute: "2-digit" }).format(new Date(t));
    tip.innerHTML = "<b>" + esc(stamp) + (tNow !== null && t > tNow ? " (ennuste)" : "") +
      "</b>" + (rows.join("") || "Ei tietoa");
    tip.hidden = false;
    const gx = X(t) * (rect.width / W);
    guide.setAttribute("x1", X(t)); guide.setAttribute("x2", X(t));
    guide.setAttribute("visibility", "visible");
    const wide = tip.offsetWidth;
    tip.style.left = Math.max(0, Math.min(rect.width - wide, gx + 12 > rect.width - wide ? gx - wide - 12 : gx + 12)) + "px";
  };
  const hideTip = () => { tip.hidden = true; guide.setAttribute("visibility", "hidden"); };
  svg.addEventListener("pointermove", showTip);
  svg.addEventListener("pointerdown", showTip);
  svg.addEventListener("pointerleave", hideTip);

  const items = series.map((s) =>
    '<li><span class="swatch" style="background:' + s.color + '"></span>' +
    esc(s.label) + "</li>");
  if (hasPrice) items.push(
    '<li><span class="swatch" style="background:' + PRICE_SERIES.color +
    '"></span>' + esc(PRICE_SERIES.label) + "</li>");
  if (marks.length) items.push(
    '<li><span class="swatch" style="background:var(--text2);height:.7rem;width:.2rem">' +
    "</span>Komennot</li>");
  $histLegend.innerHTML = items.join("");
  drawEnergy(data.energy || []);
}

const eurFmt = new Intl.NumberFormat("fi-FI",
  { style: "currency", currency: "EUR" });
const kwhFmt = new Intl.NumberFormat("fi-FI", { maximumFractionDigits: 1 });

function drawEnergy(days) {
  if (!days.length) {
    $histEnergy.innerHTML = '<p class="detail">Energian kulutus ja kustannus: ei tiedossa.</p>';
    return;
  }
  const shown = days.slice(-31);
  $histEnergy.innerHTML = (days.length > shown.length
    ? '<p class="detail">Viimeiset 31 päivää.</p>' : "") +
    "<table><thead><tr><th>Päivä</th><th>kWh</th>" +
    "<th>Kustannus</th></tr></thead><tbody>" + shown.map((d) =>
    "<tr><td>" + esc(d.date) + "</td><td>" + kwhFmt.format(d.kwh) + "</td><td>" +
    (d.cost_eur === null ? "ei tiedossa"
      : eurFmt.format(d.cost_eur) + (d.cost_complete ? "" : " (osa tunneista)")) +
    "</td></tr>").join("") + "</tbody></table>";
}

let histRequest = 0;
let histAbort = null;
async function loadHistory() {
  const request = ++histRequest;
  if (histAbort) histAbort.abort();
  const unit = $histUnit.value;
  const range = $histRange.value;
  const site = $histSite.value;
  if (!unit) { $histChart.innerHTML = ""; $histLegend.textContent = ""; $histEnergy.textContent = ""; return; }
  histAbort = new AbortController();
  $histStatus.textContent = "Haetaan\u2026";
  try {
    const url = "api/history?unit=" + encodeURIComponent(unit) +
      "&range=" + encodeURIComponent(range) +
      "&site=" + encodeURIComponent(site) +
      (histEnd === null ? "" : "&end=" + encodeURIComponent(new Date(histEnd).toISOString()));
    const data = await fetch(url, { signal: histAbort.signal }).then((r) => {
      if (!r.ok) throw new Error("http " + r.status);
      return r.json();
    });
    if (request !== histRequest) return;
    PRICE_SERIES.vat = data.vat_percent;
    PRICE_SERIES.label = data.vat_percent == null ? "Sähkön hinta (snt/kWh)"
      : "Sähkön hinta (snt/kWh, ALV " + String(data.vat_percent).replace(".", ",") + " % sis.)";
    histNowMs = new Date(data.now).getTime();
    histMaxEnd = new Date(data.max_end).getTime();
    const shownEnd = new Date(data.end).getTime();
    $histNext.disabled = shownEnd >= histMaxEnd - 1000;
    $histNow.disabled = histEnd === null;
    drawHistory(data);
    $histStatus.textContent = "";
  } catch (err) {
    if (request === histRequest && err.name !== "AbortError")
      $histStatus.textContent = "Historian haku ep\u00e4onnistui.";
  } finally {
    if (request === histRequest) histAbort = null;
  }
}

// Extra history choices (the cottage rooms) come from the cottage section.
let extraHistUnits = [];
function setExtraHistUnits(units) {
  extraHistUnits = units;
  populateUnits(lastView ? lastView.units || [] : []);
}
// Rebuilt only when the choices change, so an open list is not closed.
function populateUnits(units) {
  const all = units.concat(extraHistUnits);
  const html = all.map((u) =>
    '<option value="' + esc(u.id) + '">' + esc(u.name) + "</option>").join("");
  if (html === $histUnit.dataset.options) return;
  const current = $histUnit.value;
  $histUnit.innerHTML = html;
  $histUnit.dataset.options = html;
  if (current && all.some((u) => u.id === current)) $histUnit.value = current;
}

async function refresh() {
  try {
    const [view, events] = await Promise.all([
      fetch("api/view").then((r) => { if (!r.ok) throw new Error("http " + r.status); return r.json(); }),
      fetch("api/events").then((r) => { if (!r.ok) throw new Error("http " + r.status); return r.json(); })
    ]);
    renderView(view);
    renderEvents(events);
  } catch (err) {
    vkHealth.viewFailed();
  }
}

async function loadIdentity() {
  try {
    const id = await fetch("api/identity").then((r) => {
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
    const res = await fetch("api/command", {
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
    alert("Yhteys palveluun epäonnistui. Komentoa ei lähetetty eikä sitä lähetetä myöhemmin; yritä uudelleen, kun yhteys toimii.");
  } finally {
    sending = false;
  }
}

function onControl(e) {
  const el = e.target.closest("[data-action]");
  if (!el) return;
  const unitId = el.dataset.unit;
  const action = el.dataset.action;
  if (action === "setpoint-select") {
    // A click only opens the list; send once the value has been chosen.
    if (e.type === "change") {
      sendCommand(unitId, "setpoint", Number(el.value));
      el.blur();
    }
    return;
  }
  if (el.tagName === "SELECT") {
    if (el.value === el.dataset.lastValue) return;
    el.dataset.lastValue = el.value;
    sendCommand(unitId, action, el.value);
    el.blur();
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
// A select being used keeps its list open for up to 30 s; choosing a value
// (change) or the next refresh after that releases the hold.
const SELECT_HOLD_MS = 30000;
function holdSelect(e) {
  if (e.target.tagName === "SELECT") selectHoldUntil = Date.now() + SELECT_HOLD_MS;
}
["pointerdown", "touchstart", "focusin", "keydown"].forEach((type) =>
  $cards.addEventListener(type, holdSelect, { passive: true }));
$cards.addEventListener("change", (e) => {
  if (e.target.tagName === "SELECT") selectHoldUntil = 0;
});
// Master switch of the automation, in the page header.
const $auto = document.getElementById("auto-btn");
function showAutomation(state) {
  $auto.hidden = !state.available;
  $auto.setAttribute("aria-checked", state.on ? "true" : "false");
}
async function loadAutomation() {
  try { showAutomation(await fetch("api/automation").then((r) => r.json())); }
  catch (err) { /* the status badge already shows lost connection */ }
}
$auto.addEventListener("click", async () => {
  const on = $auto.getAttribute("aria-checked") !== "true";
  $auto.disabled = true;
  try {
    const r = await fetch("api/automation", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ on: on }) });
    if (r.ok) showAutomation(await r.json());
  } catch (err) { /* keep the old state */ }
  $auto.disabled = false;
  if (typeof loadPlan === "function") loadPlan();
});
loadAutomation();
setInterval(loadAutomation, 15000);
$histUnit.addEventListener("change", () => {
  // Cottage rooms use the cottage's outdoor temperature; pumps the home's.
  $histSite.value = $histUnit.value.startsWith("cottage:") ? "cottage" : "home";
  loadHistory();
});
$histRange.addEventListener("change", () => { histEnd = null; loadHistory(); });
function shiftHistory(dir) {
  const span = RANGE_MS[$histRange.value] || RANGE_MS.day;
  const cur = histEnd === null ? histNowMs : histEnd;
  histEnd = Math.min(cur + dir * span / 2, histMaxEnd);
  if (histEnd >= histNowMs - 1000 && histEnd <= histNowMs + 1000) histEnd = null;
  loadHistory();
}
$histPrev.addEventListener("click", () => shiftHistory(-1));
$histNext.addEventListener("click", () => shiftHistory(1));
$histNow.addEventListener("click", () => { histEnd = null; loadHistory(); });
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
if ("serviceWorker" in navigator) {
  // Offline start only; the worker never caches /api/ or touches commands.
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("sw.js", { scope: "./" }).catch(() => {});
  });
}
</script>
<script>/*SETTINGS_SCRIPT*/</script>
<script>/*PLAN_SCRIPT*/</script>
<script>/*ROOMS_SCRIPT*/</script>
<script>/*COTTAGE_SCRIPT*/</script>
</body>
</html>
"""


_HEALTH_SCRIPT = r"""
// Site health row (card H3.1): Koti from /api/view, Mökki from /api/sites and
// /api/cottage. When the browser is offline or the last refresh failed the
// chips show that instead of an old "ok", and the values are greyed out.
var vkHealth = (function () {
  "use strict";
  var S = { view: null, viewAt: null, viewFailed: false, sites: null,
            cottage: null, sitesAt: null, sitesFailed: false, open: null };
  var $home = document.getElementById("health-home");
  var $cot = document.getElementById("health-cottage");
  var $detail = document.getElementById("health-detail");
  var $banner = document.getElementById("offline-banner");
  var $upd = document.getElementById("last-updated");
  var HOME = { ok: ["ok", "ok"], stale: ["warn", "vanhentunut"],
               error: ["err", "virhe"], initializing: ["info", "käynnistyy"] };
  var COT = { ok: ["ok", "ok"], stale: ["warn", "vanhentunut"],
              offline: ["err", "ei yhteyttä"], auth_failed: ["err", "tunnus hylätty"] };
  function ago(ms) {
    if (!ms) return "ei tietoa";
    var s = Math.max(0, (Date.now() - ms) / 1000);
    if (s < 60) return "juuri nyt";
    if (s < 3600) return Math.round(s / 60) + " min sitten";
    if (s < 86400) return Math.round(s / 3600) + " h sitten";
    return Math.round(s / 86400) + " pv sitten";
  }
  function whenIso(iso) { return iso ? ago(new Date(iso).getTime()) : "ei tiedossa"; }
  function browserOffline() { return navigator.onLine === false; }
  function down() { return browserOffline() || S.viewFailed; }
  function set(chip, cls, text, detail) {
    chip.className = "badge " + cls;
    chip.textContent = text;
    chip.title = detail;
    chip.dataset.detail = detail;
  }
  function home() {
    if (down() || !S.view) {
      var known = S.viewAt ? "Viimeisin tieto saatu " + ago(S.viewAt) + "." : "Tietoa ei ole vielä saatu.";
      set($home, "mute", S.view ? "Koti: ei tietoa" : "Koti: yhdistetään…",
        down() ? "Villa Kuppisiin ei saada yhteyttä, joten kodin tilaa ei tiedetä. " + known : known);
      return;
    }
    var svc = S.view.service, c = HOME[svc.state] || HOME.initializing;
    var detail = {
      ok: "Pumppujen tiedot ovat tuoreita (päivitetty " + whenIso(svc.last_ok) + ").",
      stale: "Pumppujen lukemia ei ole saatu vähään aikaan; näytetyt arvot voivat olla vanhoja (viimeksi " +
             whenIso(svc.last_ok) + ").",
      error: "Pumppujen pilvipalvelun luku epäonnistui" + (svc.error ? ": " + svc.error : "") + ".",
      initializing: "Palvelu käynnistyy ja hakee ensimmäisiä lukemia."
    }[svc.state] || "";
    set($home, c[0], "Koti: " + c[1], detail);
  }
  function fallbackRooms() {
    var out = [];
    if (S.cottage && S.cottage.configured) S.cottage.rooms.forEach(function (r) {
      if (r.status && r.status.mode === "fallback") out.push(r.name);
    });
    return out;
  }
  function cottage() {
    var site = S.sites && S.sites[0];
    if (!site) { $cot.hidden = true; return; }
    $cot.hidden = false;
    if (down() || S.sitesFailed) {
      set($cot, "mute", "Mökki: ei tietoa",
        "Mökin tilaa ei tiedetä, koska Villa Kuppisiin ei saada yhteyttä. Viimeisin tieto saatu " +
        ago(S.sitesAt) + ".");
      return;
    }
    var conn = COT[site.connection] || COT.offline;
    var last = "Viimeksi yhteydessä " + whenIso(site.last_contact) + ".";
    var fb = fallbackRooms();
    if (site.connection === "offline" || site.connection === "auth_failed") {
      set($cot, conn[0], "Mökki: " + conn[1],
        (site.connection === "auth_failed"
          ? "Home Assistant hylkäsi tunnuksen; tunnus pitää uusia. "
          : "Yhteys mökin Home Assistantiin on poikki. Mökin ohjain toimii omillaan ja siirtyy turvalämpöön, jos Villa Kuppiksen tavoite vanhenee. ") +
        (site.error ? site.error + " " : "") + last);
    } else if (fb.length) {
      set($cot, "warn", "Mökki: turvalämpö",
        "Villa Kuppiksen tavoite puuttuu tai on vanhentunut, ja patterit ovat turvalämmöllä: " +
        fb.join(", ") + ". " + last);
    } else if (site.connection === "stale") {
      set($cot, "warn", "Mökki: vanhentunut",
        "Mökiltä ei ole saatu tuoretta tietoa vähään aikaan; näytetyt arvot voivat olla vanhoja. " + last);
    } else {
      set($cot, "ok", "Mökki: ok", "Yhteys mökin Home Assistantiin toimii. " + last);
    }
  }
  function render() {
    var isDown = down();
    document.body.dataset.conn = isDown ? "down" : "up";
    home();
    cottage();
    if (isDown) {
      $banner.hidden = false;
      $banner.textContent = (browserOffline()
        ? "Ei yhteyttä Villa Kuppisiin – näytetään viimeisin tieto"
        : "Palveluun ei saada yhteyttä – näytetään viimeisin tieto") +
        (S.viewAt ? " (päivitetty " + ago(S.viewAt) + ")." : " (tietoa ei ole vielä saatu).") +
        " Komentoja ei voi lähettää.";
      $upd.textContent = S.viewAt ? "Päivitetty " + ago(S.viewAt) : "";
    } else {
      $banner.hidden = true;
      var ok = S.view && S.view.service.last_ok;
      $upd.textContent = ok ? "Päivitetty " + whenIso(ok) : "";
    }
    var chip = S.open === "home" ? $home : S.open === "cottage" ? $cot : null;
    if (chip && !chip.hidden) { $detail.textContent = chip.dataset.detail || ""; $detail.hidden = false; }
    else { $detail.hidden = true; }
  }
  function toggle(name) { S.open = S.open === name ? null : name; render(); }
  $home.addEventListener("click", function () { toggle("home"); });
  $cot.addEventListener("click", function () { toggle("cottage"); });
  function getJson(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) throw new Error("http " + r.status);
      return r.json();
    });
  }
  function loadSites() {
    Promise.all([getJson("api/sites"), getJson("api/cottage")]).then(function (res) {
      S.sites = res[0]; S.cottage = res[1]; S.sitesAt = Date.now(); S.sitesFailed = false;
      render();
    }).catch(function () { S.sitesFailed = true; render(); });
  }
  window.addEventListener("online", render);
  window.addEventListener("offline", render);
  setInterval(render, 10000);
  setInterval(loadSites, 10000);
  loadSites();
  return {
    viewOk: function (view) { S.view = view; S.viewAt = Date.now(); S.viewFailed = false; render(); },
    viewFailed: function () { S.viewFailed = true; render(); },
    homeText: function (state) { return "Koti: " + (HOME[state] || HOME.initializing)[1]; }
  };
})();
"""

def render_page(demo: bool) -> str:
    page = (_PAGE.replace("/*SETTINGS_STYLE*/", _SETTINGS_STYLE)
            .replace("/*PLAN_STYLE*/", _PLAN_STYLE)
            .replace("<!--PLAN_SECTION-->", _PLAN_HTML)
            .replace("/*PLAN_SCRIPT*/", _PLAN_SCRIPT)
            .replace("/*HEALTH_SCRIPT*/", _HEALTH_SCRIPT)
            .replace("/*COTTAGE_STYLE*/", cottage_ui.STYLE)
            .replace("<!--COTTAGE_SECTION-->", cottage_ui.SECTION_HTML)
            .replace("/*COTTAGE_SCRIPT*/", cottage_ui.SCRIPT)
            .replace("/*ROOMS_STYLE*/", _ROOMS_STYLE)
            .replace("<!--ROOMS_SECTION-->", _ROOMS_HTML)
            .replace("/*ROOMS_SCRIPT*/", _ROOMS_SCRIPT)
            .replace("<!--SETTINGS_SECTION-->", _SETTINGS_HTML)
            .replace("/*SETTINGS_SCRIPT*/", _SETTINGS_SCRIPT))
    if not demo:
        return page
    return page.replace('<body data-demo="0">', '<body data-demo="1">')


# --- HTTP layer -----------------------------------------------------------------

class _PumpAppHandler(BaseHTTPRequestHandler):
    server_version = "villa-kuppis"

    @property
    def app(self) -> "PumpAppServer":
        return cast("PumpAppServer", self.server)

    def log_message(self, format, *args):  # no per-request stderr noise
        pass

    def _send(self, status: int, body: bytes, content_type: str,
              cache: str = "no-store") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
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
        peer = self.client_address[0]
        if peer not in LOOPBACK_PEERS:
            if self.app.ingress and peer == self.app.ingress_proxy:
                return self._ingress_identity()
            if peer not in self.app.trusted_proxies:
                self._json(403, {"error": "forbidden",
                                 "detail": "unknown client address"})
                return None
        host_values = self.headers.get_all("Host")
        if not host_values or len(host_values) != 1:
            self._json(403, {"error": "forbidden",
                             "detail": "exactly one Host header is required"})
            return None
        host = host_values[0].strip().lower()
        if (host in (f"127.0.0.1:{port}", f"localhost:{port}")
                and peer in LOOPBACK_PEERS):
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

    def _ingress_identity(self) -> dict | None:
        """Identity of a request through the Home Assistant ingress proxy.

        Home Assistant has already authenticated the user; the Supervisor
        drops client-supplied X-Remote-User-* headers and sets them from
        the session. The Origin, when present, must be the Home Assistant
        page itself (the proxy passes Host and Origin through unchanged).
        """
        def single(name):
            values = self.headers.get_all(name) or []
            if len(values) > 1:
                return False
            value = values[0].strip() if values else ""
            if _has_control_chars(value):
                return False
            return value
        user_id, login = single("X-Remote-User-Id"), single("X-Remote-User-Name")
        display, host = single("X-Remote-User-Display-Name"), single("Host")
        origin = single("Origin")
        if not user_id or login is False or display is False or not host:
            self._json(403, {"error": "forbidden",
                             "detail": "no Home Assistant user"})
            return None
        if origin is False or (origin and urlsplit(origin).netloc.lower()
                               != host.lower()):
            self._json(403, {"error": "forbidden",
                             "detail": "same origin required"})
            return None
        login = login or user_id
        actor = display or login
        if len(actor) > MAX_ACTOR_LENGTH:
            actor = login[:MAX_ACTOR_LENGTH]
        return {"actor": actor, "display_name": display or None,
                "source": "home_assistant", "login": login}

    def do_GET(self) -> None:
        identity = self._identity()
        if identity is None:
            return
        parts = urlsplit(self.path)
        if parts.path == "/":
            self._send(200, self.app.page_html.encode("utf-8"),
                       "text/html; charset=utf-8")
        elif parts.path in _STATIC_FILES:
            name, ctype, cache = _STATIC_FILES[parts.path]
            self._send(200, (_STATIC_DIR / name).read_bytes(), ctype, cache)
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
            end = None
            if qs.get("end", [""])[0]:
                try:
                    end = datetime.fromisoformat(qs["end"][0].replace("Z", "+00:00"))
                    if end.tzinfo is None:
                        raise ValueError("end needs a time zone")
                except ValueError:
                    self._json(400, {"error": "bad_query",
                                     "detail": "end must be an ISO time with a zone"})
                    return
            if not unit or not isinstance(unit, str):
                self._json(400, {"error": "bad_query",
                                 "detail": "unit is required"})
                return
            try:
                payload = self.app.service.history(unit, range_name, site, end)
            except ValueError as err:
                self._json(400, {"error": "bad_query", "detail": str(err)})
                return
            self._json(200, payload)
        elif parts.path == "/api/settings":
            self._json(200, self.app.settings_payload())
        elif parts.path == "/api/plan":
            self._json(200, self.app.service.plan_view())
        elif parts.path == "/api/away":
            self._json(200, {"away": self.app.service.automation.away()})
        elif parts.path == "/api/automation":
            self._json(200, self.app.service.automation_state())
        elif parts.path == "/api/rooms":
            self._json(200, self.app.service.room_rates())
        elif parts.path == "/api/cottage":
            self._json(200, self.app.service.cottage_view())
        elif parts.path == "/api/sites":
            self._json(200, self.app.service.sites_health())
        elif parts.path == "/api/visit":
            cottage = self._cottage_or_error()
            if cottage is not None:
                self._json(200, cottage.visit_view())
        elif parts.path == "/api/identity":
            self._json(200, {"actor": identity["actor"],
                             "display_name": identity["display_name"],
                             "source": identity["source"]})
        elif parts.path in ("/api/command", "/api/prices/refresh", "/api/visit/end"):
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
        elif parts.path == "/api/settings":
            self._post_settings(identity)
        elif parts.path == "/api/prices/refresh":
            status, payload = self.app.service.refresh_prices()
            self._json(status, payload)
        elif parts.path == "/api/away":
            body = self._read_json_object()
            if body is None:
                return
            if not isinstance(body.get("away"), bool):
                self._json(400, {"error": "bad_body",
                                 "detail": "away must be true or false"})
                return
            self._json(200, self.app.service.set_away(body["away"],
                                                      identity["actor"]))
        elif parts.path == "/api/automation":
            body = self._read_json_object()
            if body is None:
                return
            if not isinstance(body.get("on"), bool):
                self._json(400, {"error": "bad_body",
                                 "detail": "on must be true or false"})
                return
            self._json(200, self.app.service.set_automation(body["on"],
                                                            identity["actor"]))
        elif parts.path == "/api/visit":
            self._post_visit(identity)
        elif parts.path == "/api/visit/end":
            cottage = self._cottage_or_error()
            if cottage is not None:
                self._json(200, cottage.end_visit())
        elif parts.path in ("/", "/api/view", "/api/events", "/api/history",
                            "/api/identity", "/api/plan", "/api/rooms",
                            "/api/cottage", "/api/sites"):
            # Known paths with the wrong method: 405, mirroring how
            # do_GET answers /api/command.
            self._json(405, {"error": "method_not_allowed",
                             "detail": "use GET for this path"})
        elif parts.path.startswith("/api/"):
            self._json(404, {"error": "not_found",
                             "detail": "unknown API path"})
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    def _cottage_or_error(self):
        cottage = self.app.service.cottage
        if cottage is None:
            self._json(404, {"error": "not_configured",
                             "detail": "Mökkiä ei ole otettu käyttöön."})
        return cottage

    def _post_visit(self, identity) -> None:
        cottage = self._cottage_or_error()
        if cottage is None:
            return
        body = self._read_json_object()
        if body is None:
            return
        try:
            result = cottage.start_visit(body.get("arrival"), body.get("target"),
                                         body.get("rooms"), identity["actor"])
        except VisitError as error:
            self._json(400, {"error": "bad_body", "detail": str(error)})
            return
        self._json(200, result)

    def _read_json_object(self) -> dict | None:
        """Parse the JSON object body, or answer the error and return None."""
        if self.headers.get_content_type() != "application/json":
            self._json(415, {"error": "bad_body", "detail": "JSON body required"})
            return None
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY_BYTES:
            self._json(400, {"error": "bad_body",
                             "detail": "the request body is missing or too large"})
            return None
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"error": "bad_body",
                             "detail": "the request body is not valid JSON"})
            return None
        if not isinstance(body, dict):
            self._json(400, {"error": "bad_body",
                             "detail": "the request body must be a JSON object"})
            return None
        return body

    def _post_settings(self, identity) -> None:
        body = self._read_json_object()
        if body is None:
            return
        scope, key = body.get("scope"), body.get("key")
        if not isinstance(scope, str) or not isinstance(key, str):
            self._json(400, {"error": "bad_body",
                             "detail": "scope and key must be strings"})
            return
        if body.get("reset") is True:
            status, payload = self.app.change_setting(scope, key, None, True,
                                                      identity["actor"])
        elif "value" in body:
            status, payload = self.app.change_setting(scope, key,
                                                      body["value"], False,
                                                      identity["actor"])
        else:
            self._json(400, {"error": "bad_body",
                             "detail": "value or reset is required"})
            return
        self._json(status, payload)

    def _post_command(self, identity) -> None:
        body = self._read_json_object()
        if body is None:
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
                 local_actor: str = DEFAULT_LOCAL_ACTOR,
                 ingress: bool = False, trusted_proxies: tuple = ()):
        # All interfaces only inside the Home Assistant app container, where
        # every non-loopback client is refused unless it is the ingress proxy
        # or a configured Tailscale Serve proxy.
        if host not in ("127.0.0.1", "localhost") and not (
                host == "0.0.0.0" and (ingress or trusted_proxies)):
            raise ValueError("the pump app must listen on localhost")
        if trusted_proxies and public_origin is None:
            raise ValueError("a trusted proxy needs the public origin")
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
        self.ingress = ingress
        self.ingress_proxy = INGRESS_PROXY
        self.trusted_proxies = tuple(trusted_proxies)
        self.page_html = render_page(demo)
        self.settings = app_settings.SettingsStore(service.store.path)
        super().__init__((host, port), _PumpAppHandler)

    def _scopes(self) -> list[dict]:
        """Every scope the settings can apply to: sites, then home rooms."""
        scopes = [{"id": f"site:{site}", "site": site,
                   "label": app_settings.SITE_LABELS[site]}
                  for site in app_settings.SITES]
        for unit in self.service.view()["units"]:
            scopes.append({"id": f"room:home/{unit['id']}", "site": "home",
                           "label": f"Koti · {unit['name']}"})
        return scopes

    def settings_payload(self) -> dict:
        overrides = self.settings.overrides()
        scopes = self._scopes()
        effective = {}
        for s in scopes:
            site, room = app_settings.parse_scope(s["id"])
            effective[s["id"]] = app_settings.resolve(overrides, site, room)
        return {"definitions": [d.public() for d in app_settings.DEFINITIONS],
                "scopes": scopes, "overrides": overrides,
                "effective": effective, "audit": self.settings.audit()}

    def change_setting(self, scope, key, value, reset, actor):
        if scope not in {s["id"] for s in self._scopes()}:
            return 404, {"error": "not_found", "detail": "tuntematon kohde"}
        try:
            if reset:
                self.settings.reset(scope, key)
            else:
                self.settings.set(scope, key, value, actor)
        except ValueError as err:
            return 400, {"error": "bad_value", "detail": str(err)}
        return 200, {"ok": True}

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
    p.add_argument("--plan", action="store_true",
                   help="apply the price-threshold cooling plan to the "
                        "pumps (default: the plan is only shown)")
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
    # The plan loop always runs; --plan only makes the master switch start on.
    interval = DEFAULT_PLAN_INTERVAL
    if args.demo:
        service = make_demo_service(args.data_dir,
                                    automation_interval=interval,
                                    automation_default_on=args.plan)
        mode = " (esittelytila: keksityt yksiköt)"
    else:
        try:
            service = make_live_service(args.data_dir,
                                        automation_interval=interval,
                                        automation_default_on=args.plan)
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
