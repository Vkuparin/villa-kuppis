"""Finnish "today's cooling plan" section of the web app (card F4.1).

Plain ASCII JS in raw strings, spliced into the page by
``pump_app.render_page`` like the settings section.
"""

SECTION_HTML = r"""
  <section class="plan" aria-label="Päivän suunnitelma">
    <h2>Päivän suunnitelma</h2>
    <p><button type="button" class="switch" role="switch" aria-checked="false" id="away-btn"><span class="track"></span><span>Poissa kotoa</span></button></p>
    <p id="plan-note" class="detail"></p>
    <div id="plan-body"></div>
  </section>
"""

STYLE = r"""
section.plan { background: #fff; border: 1px solid #d9dee3;
               border-radius: .75rem; padding: 1rem 1.1rem;
               margin-bottom: 1rem; }
section.plan h2 { margin: 0 0 .6rem; font-size: 1.15rem; }
section.plan h3 { margin: .8rem 0 .3rem; font-size: 1rem; color: #44505c; }
section.plan ul { margin: 0; padding-left: 1.2rem; }
.plan-off { color: #a11d1d; }
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
    fetch("/api/away", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ away: !away }) })
      .then(function (r) { return r.json(); })
      .then(render).catch(function () {});
  });
  function optimizerText(o) {
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
      h.textContent = u.name + (u.active ? "" : " (ei jäähdytystilassa, ei ohjata)");
      var ul = document.createElement("ul");
      u.windows.forEach(function (w) {
        var li = document.createElement("li");
        li.textContent = hm(w.start) + "–" + hm(w.end) + " " +
          (w.off ? "pois päältä" : String(w.target_c).replace(".", ",") + " °C");
        if (w.off) li.className = "plan-off";
        ul.appendChild(li);
      });
      $body.appendChild(h);
      if (!data.away) {
        var p = document.createElement("p");
        p.className = "detail";
        p.textContent = optimizerText(u.optimizer);
        $body.appendChild(p);
      }
      $body.appendChild(ul);
    });
  }
  function load() {
    fetch("/api/plan").then(function (r) { return r.json(); })
      .then(render).catch(function () {});
  }
  load();
  setInterval(load, 60000);
})();
"""
