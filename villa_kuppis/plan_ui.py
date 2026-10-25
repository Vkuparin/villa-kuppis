"""Finnish "today's cooling plan" section of the web app (card F4.1).

Plain ASCII JS in raw strings, spliced into the page by
``pump_app.render_page`` like the settings section.
"""

SECTION_HTML = r"""
  <section class="plan" aria-label="Päivän suunnitelma">
    <h2>Päivän suunnitelma</h2>
    <p><button type="button" class="switch" role="switch" aria-checked="false" id="away-btn"><span class="track"></span><span>Poissa kotoa</span></button></p>
    <p><button type="button" id="prices-btn">Hae hinnat</button> <span id="prices-note" class="detail" role="status"></span></p>
    <p id="plan-note" class="detail"></p>
    <div id="plan-body"></div>
  </section>
"""

STYLE = r"""
section.plan { background: var(--surface); border: 1px solid var(--line);
               border-radius: .75rem; padding: 1rem 1.1rem;
               margin-bottom: 1rem; }
section.plan h2 { margin: 0 0 .6rem; font-size: 1.15rem; }
section.plan h3 { margin: .8rem 0 .3rem; font-size: 1rem; color: var(--text2); }
section.plan ul { margin: 0; padding-left: 1.2rem; }
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
    data.units.forEach(function (u) {
      var h = document.createElement("h3");
      h.textContent = u.name + (!u.active ? " (ei jäähdytystilassa, ei ohjata)"
        : u.hold_until ? " (käsiohjaus klo " + hm(u.hold_until) + " asti)" : "");
      var ul = document.createElement("ul");
      u.windows.forEach(function (w) {
        var li = document.createElement("li");
        var temp = String(w.target_c).replace(".", ",") + " °C";
        li.textContent = hm(w.start) + "–" + hm(w.end) + " " +
          (w.off ? "kallis: pumppu sammuksissa, ellei huone ylitä " + temp : temp);
        if (w.off) li.className = "plan-off";
        ul.appendChild(li);
      });
      $body.appendChild(h);
      if (!data.away) {
        var p = document.createElement("p");
        p.className = "detail";
        p.textContent = optimizerText(u);
        $body.appendChild(p);
      }
      $body.appendChild(ul);
    });
  }
  function load() {
    fetch("api/plan").then(function (r) { return r.json(); })
      .then(render).catch(function () {});
  }
  load();
  setInterval(load, 60000);
})();
"""
