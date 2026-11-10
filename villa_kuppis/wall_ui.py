"""Tablet wall view (address ``wall`` next to the app): both sites and the
coming prices in big type, for an always-on tablet.

Read-only: it polls the same GET API as the app once a minute and has no
controls, so a tablet on the wall cannot change anything by a stray touch.
It keeps the screen awake where the browser allows it (Screen Wake Lock,
HTTPS only). Plain ASCII JS in a raw string, like the other UI modules.
"""

PAGE = r"""<!doctype html>
<html lang="fi" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Villa Kuppis: seinänäkymä</title>
<meta name="theme-color" content="#0d0e10">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black">
<link rel="manifest" href="wall.webmanifest">
<link rel="icon" href="icon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="icon-192.png">
<script>
(function () {
  var a = null;
  try { a = localStorage.getItem("vk-accent-home") || localStorage.getItem("vk-accent"); } catch (e) {}
  if (a) document.documentElement.setAttribute("data-accent", a);
})();
</script>
<style>
:root {
  color-scheme: dark;
  --bg: #0d0e10; --surface: #16181b; --surface2: #1d2024; --line: #24272c;
  --text: #ecedef; --text2: #aab0b8; --text3: #80878f;
  --ok: #4ade80; --warn: #fbbf24; --err: #f87171;
  --cool: #38bdf8; --heat: #fb923c; --price: #facc15; --accent: #5b9cff;
}
:root[data-accent="green"] { --accent: #4ade80; }
:root[data-accent="purple"] { --accent: #b794f6; }
:root[data-accent="pink"] { --accent: #f472b6; }
:root[data-accent="orange"] { --accent: #fb923c; }
* { box-sizing: border-box; }
html, body { height: 100%; }
body { margin: 0; background: var(--bg); color: var(--text);
       font: 16px/1.35 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
       -webkit-font-smoothing: antialiased; font-variant-numeric: tabular-nums; }
.wall { min-height: 100%; display: grid; gap: 16px; padding: 20px;
        grid-template-rows: auto auto minmax(180px, 1fr) auto; }
.top { display: flex; justify-content: space-between; align-items: flex-end; gap: 16px;
       flex-wrap: wrap; }
.clock { font-size: clamp(56px, 9vw, 104px); font-weight: 650; letter-spacing: -.02em;
         line-height: 1; }
.date { color: var(--text2); font-size: clamp(18px, 2.2vw, 24px); margin-top: 6px; }
.now-price { text-align: right; }
.now-price .value { font-size: clamp(40px, 6vw, 72px); font-weight: 650; line-height: 1; }
.now-price .value small { font-size: .4em; font-weight: 500; color: var(--text2);
                          margin-left: .3em; }
.now-price .label { color: var(--text2); font-size: 18px; margin-top: 6px; }
.now-price .label span { white-space: nowrap; }
.sites { display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(min(100%, 420px), 1fr));
         align-content: start; }
.site { background: var(--surface); border: 1px solid var(--line); border-radius: 20px;
        padding: 18px 20px; }
.site-head { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap;
             margin-bottom: 8px; }
.site-head h2 { margin: 0; font-size: 26px; font-weight: 650; }
.site-head .outdoor { color: var(--text2); font-size: 18px; }
.badge { font-size: 15px; font-weight: 600; padding: 3px 10px; border-radius: 999px;
         background: var(--surface2); color: var(--text2); }
.badge.warn { color: var(--warn); }
.badge.err { color: var(--err); }
.badge.accent { color: var(--accent); }
.rooms { display: grid; gap: 4px 20px; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); }
.room { padding: 8px 0; }
.room .name { color: var(--text2); font-size: 18px; white-space: nowrap; overflow: hidden;
              text-overflow: ellipsis; }
.room .temp { font-size: clamp(44px, 5.5vw, 64px); font-weight: 600; line-height: 1.05; }
.room .temp.stale { color: var(--text3); }
.room .sub { color: var(--text2); font-size: 16px; }
.room .sub .cool { color: var(--cool); }
.room .sub .heat { color: var(--heat); }
.room .air { color: var(--text3); font-size: 15px; }
.room .air .warn { color: var(--warn); }
.visit { margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--line);
         color: var(--text2); font-size: 17px; }
.visit .late { color: var(--warn); }
.prices { background: var(--surface); border: 1px solid var(--line); border-radius: 20px;
          padding: 14px 20px 10px; display: flex; flex-direction: column; }
.prices[hidden] { display: none; }
.prices-head { display: flex; justify-content: space-between; color: var(--text2);
               font-size: 16px; margin-bottom: 6px; gap: 12px; flex-wrap: wrap; }
#price-chart { width: 100%; flex: 1 1 auto; min-height: 130px; display: block; }
#price-chart text { fill: var(--text3); font-size: 13px; }
.foot { display: flex; justify-content: space-between; color: var(--text3); font-size: 14px; }
.foot a { color: var(--text3); }
.foot .err { color: var(--err); }
</style>
</head>
<body>
<main class="wall">
  <div class="top">
    <div>
      <div class="clock" id="clock">--.--</div>
      <div class="date" id="date"></div>
    </div>
    <div class="now-price" id="now-price" hidden>
      <div class="value" id="price-value"></div>
      <div class="label" id="price-label">Pörssisähkö nyt</div>
    </div>
  </div>
  <div class="sites">
    <section class="site" id="home"></section>
    <section class="site" id="cottage" hidden></section>
  </div>
  <section class="prices" id="prices" hidden>
    <div class="prices-head"><span id="prices-title">Seuraavat tunnit</span><span id="prices-range"></span></div>
    <svg id="price-chart" preserveAspectRatio="none" role="img" aria-label="Pörssisähkön hinta"></svg>
  </section>
  <div class="foot"><span id="updated"></span><a href="./">Avaa Villa Kuppis</a></div>
</main>
<script>
"use strict";
var TZ = "Europe/Helsinki";
var clockFmt = new Intl.DateTimeFormat("fi-FI", { timeZone: TZ, hour: "2-digit", minute: "2-digit" });
var dateFmt = new Intl.DateTimeFormat("fi-FI", { timeZone: TZ, weekday: "long", day: "numeric", month: "numeric" });
var stampFmt = new Intl.DateTimeFormat("fi-FI", { timeZone: TZ, weekday: "short", hour: "2-digit", minute: "2-digit" });
var hourFmt = new Intl.DateTimeFormat("fi-FI", { timeZone: TZ, hour: "numeric", hourCycle: "h23" });
var one = new Intl.NumberFormat("fi-FI", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
var whole = new Intl.NumberFormat("fi-FI", { maximumFractionDigits: 0 });
var lastOk = null;
var lastPrices = null;
var cottageOverview = null, cottageOverviewAt = 0;
var COTTAGE_OVERVIEW_MS = 10 * 60 * 1000;   // only its outdoor reading is used

function el(tag, cls, text) {
  var e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
}
function deg(v) { return v === null || v === undefined ? "–" : one.format(v) + "°"; }
function get(path) {
  return fetch(path, { cache: "no-store", credentials: "same-origin" }).then(function (r) {
    if (!r.ok) throw new Error(String(r.status));
    return r.json();
  });
}
function optional(path) { return get(path).catch(function () { return null; }); }

function tick() {
  var now = new Date();
  document.getElementById("clock").textContent = clockFmt.format(now);
  var d = dateFmt.format(now);
  document.getElementById("date").textContent = d.charAt(0).toUpperCase() + d.slice(1);
  if (lastOk !== null && Date.now() - lastOk > 5 * 60 * 1000) offline();
}
function offline() {
  var u = document.getElementById("updated");
  u.className = "err";
  u.textContent = lastOk === null ? "Ei yhteyttä Villa Kuppisiin"
    : "Ei yhteyttä Villa Kuppisiin " + clockFmt.format(new Date(lastOk)) + " jälkeen";
}

function head(section, title, outdoor, badges) {
  section.textContent = "";
  var h = el("div", "site-head");
  h.appendChild(el("h2", "", title));
  if (outdoor) h.appendChild(el("span", "outdoor", "Ulkona " + deg(outdoor[1])));
  badges.forEach(function (b) { h.appendChild(el("span", "badge " + (b[1] || ""), b[0])); });
  section.appendChild(h);
  var rooms = el("div", "rooms");
  section.appendChild(rooms);
  return rooms;
}

function roomTile(name, value, fresh) {
  var r = el("div", "room");
  r.appendChild(el("div", "name", name));
  r.appendChild(el("div", "temp" + (fresh ? "" : " stale"), deg(value)));
  return r;
}

function renderHome(view, away, overview) {
  var section = document.getElementById("home");
  var badges = [];
  if (away && away.away) badges.push(["Poissa", "accent"]);
  var rooms = head(section, "Koti", overview && overview.outdoor, badges);
  var units = (view && view.units) || [];
  if (!units.length) rooms.appendChild(el("div", "room sub", "Ei pumppuja"));
  units.forEach(function (u) {
    var r = roomTile(u.name, u.room_temp, u.fresh);
    var sub = el("div", "sub");
    if (!u.fresh) sub.textContent = "ei tuoretta tietoa";
    else if (u.in_error) sub.textContent = "laitevirhe";
    else if (u.power && u.mode === "Cool") {
      sub.appendChild(el("span", "cool", "Jäähdyttää"));
      sub.appendChild(document.createTextNode(" → " + deg(u.setpoint)));
    } else if (u.power) sub.textContent = "Päällä";
    else sub.textContent = "Pois päältä";
    r.appendChild(sub);
    rooms.appendChild(r);
  });
}

function renderCottage(cottage, visitInfo, overview) {
  var section = document.getElementById("cottage");
  if (!cottage || !cottage.configured) { section.hidden = true; return; }
  section.hidden = false;
  var badges = [];
  var conn = cottage.site && cottage.site.connection;
  if (conn && conn !== "ok") badges.push(["Ei yhteyttä", "err"]);
  if (cottage.automation_on === false) badges.push(["Automaatio pois", "warn"]);
  var rooms = head(section, "Mökki", overview && overview.outdoor, badges);
  var progress = (visitInfo && visitInfo.progress) || {};
  (cottage.rooms || []).forEach(function (room) {
    var t = room.temperature;
    var fresh = !!t && t.status === "ok";
    var r = roomTile(room.name, t ? t.value : null, fresh);
    var sub = el("div", "sub");
    var plan = room.plan;
    var status = room.status || {};
    if (status.mode === "manual") sub.textContent = "Käsiohjaus";
    else if (plan && plan.target !== null && plan.target !== undefined) {
      sub.appendChild(el("span", "heat", "Tavoite " + deg(plan.target)));
      sub.appendChild(document.createTextNode(" · " + plan.reason_text));
    } else if (status.target !== null && status.target !== undefined) {
      sub.textContent = "Turvalämpö " + deg(status.target);
    }
    r.appendChild(sub);
    var air = el("div", "air");
    var parts = [];
    var limits = cottage.air_limits || {};
    if (room.humidity && room.humidity.status === "ok")
      parts.push(["kosteus " + whole.format(room.humidity.value) + " %",
                  room.humidity.value > (limits.humidity_pct || 80)]);
    if (room.co2 && room.co2.status === "ok")
      parts.push(["CO\u2082 " + whole.format(room.co2.value) + " ppm",
                  room.co2.value > (limits.co2_ppm || 1200)]);
    parts.forEach(function (p, i) {
      if (i) air.appendChild(document.createTextNode(" · "));
      air.appendChild(el("span", p[1] ? "warn" : "", p[0]));
    });
    if (parts.length) r.appendChild(air);
    var p = progress[room.id];
    if (p && p.late && !p.reached) r.appendChild(el("div", "air", "")).appendChild(
      el("span", "warn", "ei ehdi ajoissa"));
    rooms.appendChild(r);
  });
  var v = visitInfo && visitInfo.visit;
  if (v) {
    var line = el("div", "visit");
    var text = "Vierailu: saapuminen " + stampFmt.format(new Date(v.arrival));
    if (v.departure) text += ", lähtö " + stampFmt.format(new Date(v.departure));
    var plan = visitInfo.plan;
    if (v.heat_start || (plan && plan.reason === "started")) text += " · lämmitys käynnissä";
    else if (plan) text += " · lämmitys alkaa " + stampFmt.format(new Date(plan.start));
    line.textContent = text;
    section.appendChild(line);
  }
}

function renderPrices(overview) {
  var box = document.getElementById("prices");
  var top = document.getElementById("now-price");
  var rows = (overview && overview.prices) || [];
  var now = Date.now();
  var start = now - now % (15 * 60 * 1000);
  var ahead = rows.map(function (p) { return [Date.parse(p[0]), p[1]]; })
    .filter(function (p) { return p[0] >= start && p[0] < start + 24 * 3600 * 1000 && p[1] !== null; });
  if (!ahead.length || ahead[0][0] !== start) { box.hidden = true; top.hidden = true; return; }
  box.hidden = false; top.hidden = false;
  var unit = overview.vat_percent ? "snt/kWh, sis. ALV" : "snt/kWh";
  document.getElementById("price-value").innerHTML = "";
  document.getElementById("price-value").appendChild(document.createTextNode(one.format(ahead[0][1])));
  document.getElementById("price-value").appendChild(el("small", "", unit));
  var values = ahead.map(function (p) { return p[1]; });
  var lo = Math.min.apply(null, values), hi = Math.max.apply(null, values);
  // The cheapest whole hour ahead, as on the overview.
  var hours = {}, best = null;
  ahead.forEach(function (p) {
    var k = Math.floor(p[0] / 3600000);
    (hours[k] = hours[k] || []).push(p[1]);
  });
  Object.keys(hours).forEach(function (k) {
    var a = hours[k].reduce(function (x, y) { return x + y; }, 0) / hours[k].length;
    if (best === null || a < best[1]) best = [Number(k), a];
  });
  var label = document.getElementById("price-label");
  label.textContent = "";
  label.appendChild(el("span", "", "Pörssisähkö nyt"));
  label.appendChild(document.createTextNode(" · "));
  label.appendChild(el("span", "", "halvin tunti klo " + hourFmt.format(new Date(best[0] * 3600000)) +
    " (" + one.format(best[1]) + " snt)"));
  var count = Math.round(ahead.length / 4);
  document.getElementById("prices-title").textContent =
    count <= 1 ? "Seuraava tunti" : "Seuraavat " + count + " tuntia";
  document.getElementById("prices-range").textContent = one.format(lo) + "–" + one.format(hi) + " snt/kWh";
  var svg = document.getElementById("price-chart");
  var W = Math.max(300, svg.clientWidth || 800), H = Math.max(130, svg.clientHeight || 130);
  var base = H - 20, label = H - 4;
  svg.setAttribute("viewBox", "0 0 " + W + " " + H);
  var floor = Math.min(0, lo), span = Math.max(1, hi - floor);
  var bw = W / ahead.length;
  var zero = base - (0 - floor) / span * (base - 8);
  var out = "";
  ahead.forEach(function (p, i) {
    var y = base - (p[1] - floor) / span * (base - 8);
    var y0 = Math.min(y, zero), h = Math.max(1.5, Math.abs(zero - y));
    var share = hi > lo ? (p[1] - lo) / (hi - lo) : 0;
    var color = share < 0.33 ? "var(--ok)" : share < 0.66 ? "var(--price)" : "var(--err)";
    out += '<rect x="' + (i * bw + 0.5).toFixed(1) + '" y="' + y0.toFixed(1) + '" width="' +
      Math.max(1, bw - 1).toFixed(1) + '" height="' + h.toFixed(1) + '" fill="' + color +
      '" opacity="' + (i === 0 ? 1 : 0.55) + '"/>';
    var t = new Date(p[0]);
    var hour = parseInt(hourFmt.format(t), 10);
    if (t.getUTCMinutes() === 0 && hour % 3 === 0 && i * bw > 40 && i < ahead.length - 4)
      out += '<text x="' + (i * bw).toFixed(1) + '" y="' + label + '" text-anchor="middle">' +
        hour + '</text>';
  });
  out += '<text x="0" y="' + label + '">nyt</text>';
  svg.innerHTML = out;
}

function cottageOutdoor() {
  if (cottageOverview && Date.now() - cottageOverviewAt < COTTAGE_OVERVIEW_MS)
    return Promise.resolve(cottageOverview);
  return optional("api/overview?site=cottage").then(function (o) {
    if (o) { cottageOverview = o; cottageOverviewAt = Date.now(); }
    return o;
  });
}

function refresh() {
  Promise.all([get("api/view"), optional("api/away"), optional("api/cottage"),
               optional("api/visit"), optional("api/overview?site=home"),
               cottageOutdoor()]).then(function (r) {
    renderHome(r[0], r[1], r[4]);
    renderCottage(r[2], r[3], r[5]);
    lastPrices = r[4];
    renderPrices(r[4]);
    lastOk = Date.now();
    var u = document.getElementById("updated");
    u.className = "";
    u.textContent = "Päivitetty " + clockFmt.format(new Date());
  }).catch(function () { tick(); if (lastOk === null) offline(); });
}

// Keep the screen on while the page is visible (needs HTTPS; ignored elsewhere).
function keepAwake() {
  if (!("wakeLock" in navigator) || document.visibilityState !== "visible") return;
  navigator.wakeLock.request("screen").catch(function () {});
}
document.addEventListener("visibilitychange", function () {
  keepAwake();
  if (document.visibilityState === "visible") refresh();
});
window.addEventListener("resize", function () { renderPrices(lastPrices); });
tick();
refresh();
keepAwake();
setInterval(tick, 10000);
setInterval(refresh, 60000);
</script>
</body>
</html>
"""
