"""Finnish "today's cooling plan" section of the web app (card F4.1).

Plain ASCII JS in raw strings, spliced into the page by
``pump_app.render_page`` like the settings section.
"""

SECTION_HTML = r"""
  <section class="plan panel" id="plan-section" aria-label="Päivän suunnitelma">
    <div class="panel-head">
      <h2><span data-icon="calendar"></span>Päivän suunnitelma</h2>
      <div class="tools">
        <button type="button" class="switch" role="switch" aria-checked="false" id="away-btn"
          title="Poissa ollessa jäähdytetään vasta poissa-tilan enimmäislämpötilan yli eikä esijäähdytetä."><span class="track"></span><span>Poissa kotoa</span></button>
        <button type="button" id="prices-btn" title="Hakee tämän ja huomisen päivän pörssihinnat, jos niitä ei vielä ole.">Hae hinnat</button>
      </div>
    </div>
    <p id="prices-note" class="detail" role="status"></p>
    <p id="plan-note" class="detail"></p>
    <div id="plan-body"></div>
  </section>
"""

STYLE = r"""
#prices-note:empty, #plan-note:empty { display: none; }
.plan-unit { padding: .85rem 0 .2rem; border-top: 1px solid var(--line2); }
.plan-unit:first-of-type { border-top: 0; padding-top: .3rem; }
.plan-unit h3 { display: flex; flex-wrap: wrap; align-items: baseline; gap: .2rem .6rem;
                margin: 0 0 .45rem; font-size: .95rem; }
.plan-unit h3 span { font-weight: 500; font-size: .82rem; color: var(--text3); }
.timeline { position: relative; height: 34px; border-radius: 9px; overflow: hidden;
            background: var(--surface2); display: flex; }
.timeline .seg-w { position: absolute; top: 0; bottom: 0; display: flex; align-items: center;
                   justify-content: center; font-size: .75rem; font-weight: 600;
                   color: var(--text); border-right: 2px solid var(--surface);
                   font-variant-numeric: tabular-nums; overflow: hidden; white-space: nowrap; }
.timeline .seg-w.off { color: var(--err);
  background: repeating-linear-gradient(135deg, var(--err-bg) 0 6px, transparent 6px 12px); }
.timeline .now { position: absolute; top: -2px; bottom: -2px; width: 2px; background: var(--text);
                 border-radius: 1px; }
.tl-axis { position: relative; height: 18px; font-size: .7rem; color: var(--text3); }
.tl-axis span { position: absolute; transform: translateX(-50%); top: 2px; }
.tl-axis span:first-child { transform: none; }
.tl-axis span:last-child { transform: translateX(-100%); }
.plan-unit details { margin-top: .25rem; font-size: .85rem; }
.plan-unit summary { cursor: pointer; color: var(--text3); font-size: .8rem; }
section.plan ul { margin: .35rem 0 0; padding-left: 1.2rem; }
.plan-off { color: var(--err); }
"""

SCRIPT = r"""
(function () {
  "use strict";
  var $body = document.getElementById("plan-body");
  var $note = document.getElementById("plan-note");
  function hm(iso) {
    return new Date(iso).toLocaleTimeString("fi-FI",
      { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Helsinki" });
  }
  var $away = document.getElementById("away-btn");
  var away = false;
  function showAway(on) {
    away = on;
    $away.setAttribute("aria-checked", on ? "true" : "false");
  }
  $away.addEventListener("click", function () {
    fetch("api/away", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ away: !away }) })
      .then(function (r) { return r.json(); })
      .then(render).catch(function () {});
  });
  var $prices = document.getElementById("prices-btn");
  var $pricesNote = document.getElementById("prices-note");
  function dayText(d) {
    if (d.status === "not_published") return "ei vielä julkaistu";
    if (d.status === "error") return "haku epäonnistui";
    return d.periods + " jaksoa";
  }
  $prices.addEventListener("click", function () {
    $prices.disabled = true;
    $pricesNote.textContent = "Haetaan...";
    fetch("api/prices/refresh", { method: "POST" })
      .then(function (r) {
        return r.json().then(function (data) { return { ok: r.ok, data: data }; });
      })
      .then(function (res) {
        if (!res.ok) {
          $pricesNote.textContent = res.data.detail || "Hintojen haku epäonnistui.";
          return;
        }
        var days = res.data.days;
        $pricesNote.textContent = "Hinnat haettu: tänään " + dayText(days[0]) +
          ", huomenna " + dayText(days[1]);
        load();
      })
      .catch(function () { $pricesNote.textContent = "Hintojen haku epäonnistui."; })
      .then(function () { $prices.disabled = false; });
  });
  function optimizerText(u) {
    var o = u.optimizer;
    if (u.price_control === "off") return "Hintaohjaus pois: tavoite on aina enimmäislämpötila.";
    if (!o) return "Kiinteät hintarajat (optimointi pois tai huoneen jäähdytysnopeutta ei ole vielä opittu).";
    if (!o.optimized) return "Optimoija käytössä: kiinteät hintarajat ovat nyt yhtä edulliset, joten niitä noudatetaan.";
    var text = "Optimoitu opituilla nopeuksilla";
    if (o.fixed_cost_c > 0) {
      var pct = Math.round(100 * (o.fixed_cost_c - o.cost_c) / o.fixed_cost_c);
      text += ": arvioitu säästö " + pct + " % kiinteisiin hintarajoihin verrattuna";
    }
    return text + ".";
  }
  // Colder targets are deeper blue: the plan reads at a glance.
  function shade(target) {
    var k = Math.max(0.12, Math.min(0.85, (26 - target) / 6));
    return "color-mix(in srgb, var(--cool) " + Math.round(k * 100) + "%, var(--surface2))";
  }
  function timeline(windows, d0, d1) {
    var span = d1 - d0, now = Date.now();
    var bar = document.createElement("div");
    bar.className = "timeline";
    windows.forEach(function (w) {
      var a = new Date(w.start).getTime(), b = new Date(w.end).getTime();
      var seg = document.createElement("div");
      var temp = String(w.target_c).replace(".", ",") + " °C";
      seg.className = "seg-w" + (w.off ? " off" : "");
      seg.style.left = (100 * (a - d0) / span) + "%";
      seg.style.width = (100 * (b - a) / span) + "%";
      if (!w.off) seg.style.background = shade(w.target_c);
      seg.title = hm(w.start) + "–" + hm(w.end) + " " +
        (w.off ? "kallis: pumppu sammuksissa, ellei huone ylitä " + temp : temp);
      if ((b - a) / span > 0.08) seg.textContent = w.off ? "pois" : String(w.target_c).replace(".", ",") + "°";
      bar.appendChild(seg);
    });
    if (now > d0 && now < d1) {
      var n = document.createElement("div");
      n.className = "now";
      n.style.left = (100 * (now - d0) / span) + "%";
      n.title = "Nyt";
      bar.appendChild(n);
    }
    return bar;
  }
  function axis(d0, d1) {
    var ax = document.createElement("div");
    ax.className = "tl-axis";
    for (var h = 0; h <= 24; h += 6) {
      var s = document.createElement("span");
      s.style.left = (100 * h * 3600000 / (d1 - d0)) + "%";
      s.textContent = h === 24 ? "24" : ("0" + h).slice(-2);
      ax.appendChild(s);
    }
    return ax;
  }
  function render(data) {
    showAway(!!data.away);
    $body.textContent = "";
    if (!data.has_prices) {
      $note.textContent = "Päivän sähkönhintoja ei ole vielä tallennettu.";
      return;
    }
    $note.textContent = (data.away ? "Poissa-tila: jäähdytys vasta enimmäislämpötilan (poissa) yli, ei esijäähdytystä. " : "") + (data.enabled
      ? "Suunnitelma on käytössä: muutokset tehdään pumppuihin ja kirjataan tapahtumiin nimellä " + data.actor + "."
      : "Suunnitelma on vain esitys: sitä ei toteuteta pumpuissa.");
    var d0 = Infinity, d1 = -Infinity;
    data.units.forEach(function (u) {
      u.windows.forEach(function (w) {
        d0 = Math.min(d0, new Date(w.start).getTime());
        d1 = Math.max(d1, new Date(w.end).getTime());
      });
    });
    data.units.forEach(function (u) {
      var box = document.createElement("div");
      box.className = "plan-unit";
      var h = document.createElement("h3");
      h.textContent = u.name;
      var tag = !u.active ? "ei jäähdytystilassa, ei ohjata"
        : u.hold_until ? "käsiohjaus klo " + hm(u.hold_until) + " asti" : "";
      if (!data.away) tag = (tag ? tag + " · " : "") + optimizerText(u);
      if (tag) {
        var sp = document.createElement("span");
        sp.textContent = tag;
        h.appendChild(sp);
      }
      box.appendChild(h);
      if (u.windows.length && isFinite(d0)) {
        box.appendChild(timeline(u.windows, d0, d1));
        box.appendChild(axis(d0, d1));
      }
      var det = document.createElement("details");
      var sum = document.createElement("summary");
      sum.textContent = "Jaksot";
      det.appendChild(sum);
      var ul = document.createElement("ul");
      u.windows.forEach(function (w) {
        var li = document.createElement("li");
        var temp = String(w.target_c).replace(".", ",") + " °C";
        li.textContent = hm(w.start) + "–" + hm(w.end) + " " +
          (w.off ? "kallis: pumppu sammuksissa, ellei huone ylitä " + temp : temp);
        if (w.off) li.className = "plan-off";
        ul.appendChild(li);
      });
      det.appendChild(ul);
      box.appendChild(det);
      $body.appendChild(box);
    });
  }
  function load() {
    fetch("api/plan").then(function (r) { return r.json(); })
      .then(render).catch(function () {});
  }
  load();
  vkEvery(load, 60000);
})();
"""
