"""Finnish "learned room rates" section of the web app (card F5.1).

JS in raw strings, spliced into the page by
``pump_app.render_page`` like the plan section.
"""

SECTION_HTML = r"""
  <section class="rooms panel" id="rooms-section" aria-label="Huoneiden opitut nopeudet">
    <div class="panel-head">
      <h2><span data-icon="spark"></span>Huoneiden opitut nopeudet</h2>
    </div>
    <p id="rooms-note" class="detail"></p>
    <p id="rooms-forecast" class="detail"></p>
    <div id="rooms-body" class="rooms-grid"></div>
  </section>
"""

STYLE = r"""
.rooms-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
              gap: .8rem; margin-top: .8rem; }
.room-card { border: 1px solid var(--line); border-radius: 12px; padding: .85rem .95rem;
             background: var(--surface2); display: flex; flex-direction: column; gap: .55rem; }
:root[data-theme="dark"] .room-card { background: var(--bg); }
.room-card header { display: flex; justify-content: space-between; align-items: center; gap: .5rem;
                    flex-wrap: wrap; }
.room-card h3 { margin: 0; font-size: .95rem; }
.room-stats { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: .4rem; }
.room-stat { display: flex; flex-direction: column; min-width: 0; }
.room-stat b { font-size: 1.15rem; font-variant-numeric: tabular-nums; letter-spacing: -.02em; }
.room-stat b.cool { color: var(--cool); }
.room-stat b.heat { color: var(--heat); }
.room-stat span { font-size: .72rem; color: var(--text3); }
.room-card ul { margin: 0; padding-left: 1.1rem; font-size: .84rem; color: var(--text2); }
.room-card ul:empty { display: none; }
.rooms-ok { color: var(--ok); }
.rooms-bad { color: var(--err); }
"""

SCRIPT = r"""
(function () {
  "use strict";
  var $body = document.getElementById("rooms-body");
  var $note = document.getElementById("rooms-note");
  var $forecast = document.getElementById("rooms-forecast");
  function num(v, digits) {
    return v.toFixed(digits).replace(".", ",").replace("-", "−");
  }
  function fiDate(iso) {
    var p = iso.split("-");
    return Number(p[2]) + "." + Number(p[1]) + "." + p[0];
  }
  function signed(v) { return (v > 0 ? "+" : "") + num(v, 1); }
  function item(ul, text, cls) {
    var li = document.createElement("li");
    li.textContent = text;
    if (cls) li.className = cls;
    ul.appendChild(li);
  }
  function stat(box, value, label, cls, title) {
    var d = document.createElement("div");
    d.className = "room-stat";
    if (title) d.title = title;
    var b = document.createElement("b");
    b.textContent = value;
    if (cls) b.className = cls;
    var sp = document.createElement("span");
    sp.textContent = label;
    d.appendChild(b); d.appendChild(sp);
    box.appendChild(d);
  }
  function render(data) {
    $body.textContent = "";
    $note.textContent = "Opittu viimeisten " + data.learn_days +
      " päivän lukemista. Hyväksytty virhe: keskimäärin enintään " +
      num(data.limit_c, 1) + " °C edellisen päivän ennusteessa.";
    function range(rows) {
      var temps = rows.map(function (p) { return p[1]; });
      return num(Math.min.apply(null, temps), 0) + "…" +
        num(Math.max.apply(null, temps), 0) + " °C";
    }
    if (data.forecast.length) {
      $forecast.textContent = "Ilmatieteen laitoksen ennuste seuraavalle vuorokaudelle: " +
        range(data.forecast) + (data.cottage_forecast && data.cottage_forecast.length
          ? ", mökillä " + range(data.cottage_forecast) : "") + ".";
    } else {
      $forecast.textContent = "Sääennustetta ei ole vielä haettu.";
    }
    data.units.filter(function (u) {
      // Koti shows the home rooms, Mokki the cottage rooms.
      return typeof vkSite !== "function" || (u.site || "home") === vkSite();
    }).forEach(function (u) {
      // A cottage room is heated by its Mill heaters, a home room by its pump.
      var cot = u.site === "cottage";
      var card = document.createElement("article");
      card.className = "room-card";
      var head = document.createElement("header");
      var h = document.createElement("h3");
      h.textContent = u.name;
      head.appendChild(h);
      card.appendChild(head);
      var stats = document.createElement("div");
      stats.className = "room-stats";
      var ul = document.createElement("ul");
      var m = u.model;
      if (!m.ready) {
        var chip = document.createElement("span");
        chip.className = "badge info";
        chip.textContent = "Opitaan";
        head.appendChild(chip);
        item(ul, "Opitaan vielä: lukemia on " + u.hours + " tunnilta, tarvitaan vähintään " +
          (cot ? "vuorokausi aikaa, jolloin patterit eivät lämmitä."
               : "vuorokausi ilman laitteen ohjausta."));
      } else {
        if (u.now) {
          stat(stats, signed(u.now.off_c_per_h) + " °C/h",
            cot ? "patterit eivät lämmitä" : "laite pois päältä", "",
            (u.now.outdoor_c === null ? "Ulkolämpötila ei tiedossa"
              : "Ulkona " + num(u.now.outdoor_c, 1) + " °C") + ".");
          if (u.now.cooling_c_per_h !== null)
            stat(stats, signed(u.now.cooling_c_per_h) + " °C/h", "jäähdyttäessä", "cool");
          if (u.now.heating_c_per_h !== null)
            stat(stats, signed(u.now.heating_c_per_h) + " °C/h", "lämmittäessä", "heat");
        }
        if (u.predicted)
          stat(stats, num(u.predicted.off_c, 1) + " °C", u.predicted.hours + " h päästä",
            "", "Ennuste: jos " + (cot ? "patterit eivät lämmitä" : "laite on pois päältä") +
            ", huone on " + u.predicted.hours + " tunnin päästä noin " +
            num(u.predicted.off_c, 1) + " °C.");
        if (m.cool_c_per_h === null && m.heat_c_per_h === null)
          item(ul, cot ? "Patterien teho opitaan, kun ne ovat lämmittäneet vähintään kaksi tuntia."
                       : "Laitteen teho opitaan, kun se on jäähdyttänyt tai lämmittänyt vähintään kaksi tuntia.");
        if (!m.uses_outdoor)
          item(ul, "Ulkolämpötilaa ei ole tarpeeksi; malli käyttää vain huoneen omaa muutosta.");
      }
      if (u.validation) {
        var v = u.validation;
        var ok = document.createElement("span");
        ok.className = "badge " + (v.passed ? "ok" : "err");
        ok.textContent = v.passed ? "Testi hyväksytty" : "Testi yli rajan";
        ok.title = "Testipäivä " + fiDate(v.day) + ": keskivirhe " + num(v.mae_c, 2) +
          " °C, suurin " + num(v.max_error_c, 1) + " °C" +
          (v.passed ? " (hyväksytty)." : " (yli rajan).");
        head.appendChild(ok);
        item(ul, ok.title, v.passed ? "rooms-ok" : "rooms-bad");
      } else if (m.ready) {
        item(ul, "Testipäivää ei voi vielä arvioida: tarvitaan kokonainen päivä ja sitä edeltävät lukemat.");
      }
      if (stats.children.length) card.appendChild(stats);
      card.appendChild(ul);
      $body.appendChild(card);
    });
  }
  function load() {
    fetch("api/rooms").then(function (r) { return r.json(); })
      .then(render).catch(function () {});
  }
  document.addEventListener("vk-page", load);
  load();
  vkEvery(load, 600000);
})();
"""
