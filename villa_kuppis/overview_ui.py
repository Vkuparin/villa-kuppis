"""Finnish overview tiles at the top of each page: the electricity price now
and over today and tomorrow, outdoor and indoor temperatures, and the
devices at a glance.

JS in raw strings, spliced into the page by ``pump_app.render_page`` like
the other sections.
"""

SECTION_HTML = r"""
  <section id="overview" class="kpis" aria-label="Yleiskatsaus"></section>
"""

STYLE = r"""
.kpis { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 1rem; }
.kpis:empty { display: none; }
.kpi { padding: 1rem 1.15rem; display: flex; flex-direction: column; gap: .15rem; min-width: 0; }
.kpi.hero { grid-column: span 2; grid-row: span 2; padding: 1.15rem 1.25rem; }
.kpi-label { display: flex; align-items: center; gap: .45rem; font-size: .82rem; font-weight: 600;
             color: var(--text2); }
.kpi-label .ico { width: 16px; height: 16px; color: var(--text3); }
.kpi-big { font-size: 1.9rem; font-weight: 700; letter-spacing: -.03em; line-height: 1.15;
           font-variant-numeric: tabular-nums; margin-top: .25rem; }
.kpi-big small { font-size: .95rem; font-weight: 600; color: var(--text3); margin-left: .25rem;
                 letter-spacing: 0; }
.kpi.hero .kpi-big { font-size: 3.1rem; }
.kpi-sub { font-size: .8rem; color: var(--text3); }
.kpi-row { display: flex; flex-wrap: wrap; align-items: center; gap: .4rem .6rem; margin-top: .35rem; }
.kpi .spark { width: 100%; height: 34px; margin-top: auto; padding-top: .4rem; }
.kpi .spark path { fill: none; stroke: var(--cool); stroke-width: 2; stroke-linecap: round;
                   stroke-linejoin: round; vector-effect: non-scaling-stroke; }
.price-chart { margin-top: auto; padding-top: .9rem; }
.price-chart svg { width: 100%; height: 132px; display: block; overflow: visible; }
.price-chart .b-lo { fill: var(--ok); }
.price-chart .b-mid { fill: var(--price); }
.price-chart .b-hi { fill: var(--err); }
.price-chart .past { opacity: .3; }
.price-chart text { font-size: 10.5px; fill: var(--text3); }
.price-legend { display: flex; flex-wrap: wrap; gap: .3rem .9rem; margin-top: .35rem;
                font-size: .75rem; color: var(--text3); }
.price-legend i { display: inline-block; width: .55rem; height: .55rem; border-radius: 2px;
                  margin-right: .35rem; }
@media (max-width: 1000px) {
  .kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .75rem; }
  .kpi.hero { grid-row: auto; }
  .kpi.hero .kpi-big { font-size: 2.5rem; }
  .kpi-big { font-size: 1.6rem; }
}
"""

SCRIPT = r"""
(function () {
  "use strict";
  var $box = document.getElementById("overview");
  var one = new Intl.NumberFormat("fi-FI", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  var cents = new Intl.NumberFormat("fi-FI", { minimumFractionDigits: 1, maximumFractionDigits: 2 });
  var hourFmt = new Intl.DateTimeFormat("fi-FI", { timeZone: "Europe/Helsinki", hour: "2-digit" });
  var data = null;
  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function tile(cls, icon, label, big, unit, sub, extra, title) {
    return '<article class="card kpi ' + cls + '"' + (title ? ' title="' + esc(title) + '"' : "") + ">" +
      '<span class="kpi-label">' + vkIcon(icon) + esc(label) + "</span>" +
      '<span class="kpi-big">' + big + (unit ? "<small>" + esc(unit) + "</small>" : "") + "</span>" +
      (sub ? '<span class="kpi-sub">' + sub + "</span>" : "") + (extra || "") + "</article>";
  }
  function t(v) { return v === null || v === undefined ? "–" : one.format(v); }
  // Cheap, middle and dear thirds of the shown prices.
  function levels(values) {
    var s = values.slice().sort(function (a, b) { return a - b; });
    return [s[Math.floor(s.length / 3)], s[Math.floor(2 * s.length / 3)]];
  }
  function levelOf(v, cut) { return v <= cut[0] ? "lo" : v >= cut[1] ? "hi" : "mid"; }
  var LEVEL_TEXT = { lo: ["ok", "Halpa"], mid: ["warn", "Kohtuullinen"], hi: ["err", "Kallis"] };
  function priceChart(pts, d0, d1, now, cut) {
    var W = 600, H = 132, top = 6, bottom = 18;
    var max = Math.max.apply(null, pts.map(function (p) { return p[1]; }).concat([1]));
    var min = Math.min.apply(null, pts.map(function (p) { return p[1]; }).concat([0]));
    var span = d1 - d0, step = 900000;
    function X(ms) { return (ms - d0) / span * W; }
    function Y(v) { return top + (1 - (v - min) / (max - min)) * (H - top - bottom); }
    var s = '<svg viewBox="0 0 ' + W + " " + H + '" preserveAspectRatio="none" role="img" ' +
      'aria-label="Sähkön hinta tänään ja huomenna">';
    pts.forEach(function (p) {
      var x0 = X(p[0]), x1 = X(p[0] + step), y = Y(Math.max(p[1], 0)), y0 = Y(Math.min(p[1], 0));
      s += '<rect class="b-' + levelOf(p[1], cut) + (p[0] + step <= now ? " past" : "") +
        '" x="' + x0.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' +
        Math.max(0.5, x1 - x0 - 0.8).toFixed(1) + '" height="' + Math.max(0.8, y0 - y).toFixed(1) +
        '" rx="1"><title>' + hourFmt.format(new Date(p[0])) + ":" +
        ("0" + new Date(p[0]).getUTCMinutes()).slice(-2) + " " + cents.format(p[1]) +
        " snt/kWh</title></rect>";
    });
    for (var h = 0; h * 3600000 < span; h += 6) {
      var ms = d0 + h * 3600000, x = X(ms);
      var lab = h % 24 === 0 && h > 0 ? "huomenna" : ("0" + (h % 24)).slice(-2);
      s += '<text x="' + (x + 2).toFixed(1) + '" y="' + (H - 4) + '">' + lab + "</text>";
      if (h % 24 === 0 && h > 0)
        s += '<line x1="' + x.toFixed(1) + '" x2="' + x.toFixed(1) + '" y1="0" y2="' + (H - bottom) +
          '" stroke="var(--line3)" stroke-dasharray="3 3"/>';
    }
    if (now > d0 && now < d1)
      s += '<line x1="' + X(now).toFixed(1) + '" x2="' + X(now).toFixed(1) + '" y1="0" y2="' +
        (H - bottom) + '" stroke="var(--text)" stroke-width="1.5"/>';
    return s + "</svg>";
  }
  function priceTiles(now) {
    var all = data.prices || [];
    var pts = all.map(function (p) { return [new Date(p[0]).getTime(), p[1]]; });
    var d0 = new Date(data.day_start).getTime(), d1 = new Date(data.day_end).getTime();
    var mid = d0 + (d1 - d0) / 2;
    var today = pts.filter(function (p) { return p[0] < mid; });
    var tomorrow = pts.filter(function (p) { return p[0] >= mid; });
    var vat = data.vat_percent === null ? "ilman ALV:tä"
      : "sis. ALV " + String(data.vat_percent).replace(".", ",") + " %";
    var hero, next;
    if (!today.length) {
      hero = tile("hero", "bolt", "Sähkö nyt", "–", "", "Päivän hintoja ei ole vielä haettu.");
    } else {
      var shownEnd = tomorrow.length ? d1 : mid;
      var shown = tomorrow.length ? pts : today;
      var cut = levels(shown.map(function (p) { return p[1]; }));
      var cur = null;
      shown.forEach(function (p) { if (p[0] <= now) cur = p; });
      var vals = today.map(function (p) { return p[1]; });
      var avg = vals.reduce(function (a, b) { return a + b; }, 0) / vals.length;
      // Cheapest whole hour still ahead.
      var hours = {};
      shown.forEach(function (p) {
        if (p[0] + 900000 <= now) return;
        var k = Math.floor(p[0] / 3600000);
        (hours[k] = hours[k] || []).push(p[1]);
      });
      var best = null;
      Object.keys(hours).forEach(function (k) {
        var a = hours[k].reduce(function (x, y) { return x + y; }, 0) / hours[k].length;
        if (best === null || a < best[1]) best = [Number(k), a];
      });
      var lvl = cur ? LEVEL_TEXT[levelOf(cur[1], cut)] : null;
      hero = tile("hero", "bolt", "Sähkö nyt", cur ? cents.format(cur[1]) : "–", "snt/kWh",
        "Tänään " + cents.format(Math.min.apply(null, vals)) + "–" +
        cents.format(Math.max.apply(null, vals)) + " snt, keskiarvo " + cents.format(avg) +
        " snt · " + vat,
        '<div class="kpi-row">' + (lvl ? '<span class="badge ' + lvl[0] + '">' + lvl[1] + "</span>" : "") +
        (best ? '<span class="kpi-sub">Halvin tuleva tunti klo ' +
          hourFmt.format(new Date(best[0] * 3600000)) + " · " + cents.format(best[1]) + " snt</span>" : "") +
        "</div>" +
        '<div class="price-chart">' + priceChart(shown, d0, shownEnd, now, cut) +
        '<div class="price-legend"><span><i style="background:var(--ok)"></i>Halvin kolmannes</span>' +
        '<span><i style="background:var(--price)"></i>Keskimmäinen</span>' +
        '<span><i style="background:var(--err)"></i>Kallein kolmannes</span></div></div>',
        "Pörssisähkön hinta. Pylväät ovat 15 minuutin jaksoja; viiva on nyt.");
    }
    if (tomorrow.length) {
      var tv = tomorrow.map(function (p) { return p[1]; });
      var tavg = tv.reduce(function (a, b) { return a + b; }, 0) / tv.length;
      next = tile("", "calendar", "Huomenna", cents.format(tavg), "snt/kWh",
        "keskiarvo · " + cents.format(Math.min.apply(null, tv)) + "–" +
        cents.format(Math.max.apply(null, tv)) + " snt");
    } else {
      next = tile("", "calendar", "Huomenna", "–", "",
        "Huomisen hinnat julkaistaan yleensä iltapäivällä.");
    }
    return [hero, next];
  }
  function spark(rows) {
    if (rows.length < 2) return "";
    var v = rows.map(function (r) { return r[1]; });
    var lo = Math.min.apply(null, v), hi = Math.max.apply(null, v);
    if (hi - lo < 1) { lo -= .5; hi += .5; }
    var d = v.map(function (y, i) {
      return (i ? "L" : "M") + (i / (v.length - 1) * 100).toFixed(1) + " " +
        (30 - (y - lo) / (hi - lo) * 26).toFixed(1);
    }).join("");
    return '<svg class="spark" viewBox="0 0 100 32" preserveAspectRatio="none" aria-hidden="true">' +
      '<path d="' + d + '"/></svg>';
  }
  function outdoorTile() {
    var fc = data.forecast || [];
    var now = data.outdoor ? data.outdoor[1] : null;
    var vals = fc.map(function (r) { return r[1]; });
    var sub = vals.length ? "Ennuste 24 h: " + t(Math.min.apply(null, vals)) + "…" +
      t(Math.max.apply(null, vals)) + " °C" : "Ennustetta ei ole vielä haettu.";
    if (now === null && vals.length) { now = vals[0]; sub = "Ennuste · " + sub; }
    return tile("", "cloud", "Ulkona", t(now), now === null ? "" : "°C", sub, spark(fc),
      "Ilmatieteen laitoksen havainto ja ennuste");
  }
  function indoorTiles() {
    var out = [];
    if (vkSite() === "cottage") {
      var c = window.vkCottageData;
      var rooms = c && c.configured ? c.rooms : [];
      var temps = rooms.filter(function (r) { return r.temperature && r.temperature.value !== null; });
      out.push(tile("", "thermo", "Sisällä",
        temps.length ? t(temps.reduce(function (a, r) { return a + r.temperature.value; }, 0) / temps.length) : "–",
        temps.length ? "°C" : "",
        temps.map(function (r) { return esc(r.name) + " " + t(r.temperature.value) + " °C"; }).join(" · ") ||
        "Ei lukemia."));
      var conn = c && c.configured ? c.site.connection : null;
      var CONN = { ok: "Yhteys ok", stale: "Vanhentunut", offline: "Ei yhteyttä",
                   auth_failed: "Tunnus hylätty" };
      out.push(tile("", "link", "Mökin yhteys", conn ? esc(CONN[conn] || conn) : "–", "",
        rooms.length ? rooms.length + " huonetta" : "Mökkiä ei ole liitetty."));
      return out;
    }
    var units = (typeof lastView !== "undefined" && lastView && lastView.units) || [];
    var withTemp = units.filter(function (u) { return u.room_temp !== null && u.room_temp !== undefined; });
    var avg = withTemp.length ? withTemp.reduce(function (a, u) { return a + u.room_temp; }, 0) / withTemp.length : null;
    var sorted = withTemp.slice().sort(function (a, b) { return b.room_temp - a.room_temp; });
    out.push(tile("", "thermo", "Sisällä", t(avg), avg === null ? "" : "°C",
      sorted.length > 1 ? "Lämpimin " + esc(sorted[0].name) + " " + t(sorted[0].room_temp) + " °C"
        : sorted.length ? esc(sorted[0].name) : "Ei lukemia.", "", "Pumppujen mittaamien lämpötilojen keskiarvo"));
    var on = units.filter(function (u) { return u.power === true; }).length;
    var auto = typeof autoState !== "undefined" && autoState ? autoState.on : null;
    out.push(tile("", "fan", "Pumput", units.length ? on + "<small>/ " + units.length + " päällä</small>" : "–", "",
      auto === null ? "" : "Automaatio " + (auto ? "päällä" : "pois päältä")));
    return out;
  }
  function render() {
    if (!data || data.site !== vkSite()) return;
    var now = Date.now();
    var p = priceTiles(now);
    var rest = indoorTiles();
    $box.innerHTML = p[0] + outdoorTile() + rest[0] + p[1] + rest[1];
  }
  function load() {
    fetch("api/overview?site=" + encodeURIComponent(vkSite()))
      .then(function (r) { if (!r.ok) throw new Error("http " + r.status); return r.json(); })
      .then(function (d) { data = d; render(); }).catch(function () {});
  }
  document.addEventListener("vk-page", function () { $box.textContent = ""; load(); });
  load();
  vkEvery(load, 300000);
  vkEvery(render, 5000);
})();
"""
