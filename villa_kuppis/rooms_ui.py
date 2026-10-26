"""Finnish "learned room rates" section of the web app (card F5.1).

JS in raw strings, spliced into the page by
``pump_app.render_page`` like the plan section.
"""

SECTION_HTML = r"""
  <section class="rooms" aria-label="Huoneiden opitut nopeudet">
    <h2>Huoneiden opitut nopeudet</h2>
    <p id="rooms-note" class="detail"></p>
    <p id="rooms-forecast" class="detail"></p>
    <div id="rooms-body"></div>
  </section>
"""

STYLE = r"""
section.rooms { background: var(--surface); border: 1px solid var(--line);
                border-radius: .75rem; padding: 1rem 1.1rem;
                margin-bottom: 1rem; }
section.rooms h2 { margin: 0 0 .6rem; font-size: 1.15rem; }
section.rooms h3 { margin: .8rem 0 .3rem; font-size: 1rem; color: var(--text2); }
section.rooms ul { margin: 0; padding-left: 1.2rem; }
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
      var h = document.createElement("h3");
      h.textContent = u.name;
      var ul = document.createElement("ul");
      var m = u.model;
      if (!m.ready) {
        item(ul, "Opitaan vielä: lukemia on " + u.hours + " tunnilta, tarvitaan vähintään " +
          (cot ? "vuorokausi aikaa, jolloin patterit eivät lämmitä."
               : "vuorokausi ilman laitteen ohjausta."));
      } else {
        if (u.now) {
          item(ul, (cot ? "Patterit eivät lämmitä: " : "Laite pois päältä: ") +
            signed(u.now.off_c_per_h) +
            " °C/h nyt (" + (u.now.outdoor_c === null ? "ulkolämpötila ei tiedossa"
              : "ulkona " + num(u.now.outdoor_c, 1) + " °C") + ").");
          if (u.now.cooling_c_per_h !== null)
            item(ul, "Jäähdyttäessä: " + signed(u.now.cooling_c_per_h) + " °C/h.");
          if (u.now.heating_c_per_h !== null)
            item(ul, "Lämmittäessä: " + signed(u.now.heating_c_per_h) + " °C/h.");
        }
        if (m.cool_c_per_h === null && m.heat_c_per_h === null)
          item(ul, cot ? "Patterien teho opitaan, kun ne ovat lämmittäneet vähintään kaksi tuntia."
                       : "Laitteen teho opitaan, kun se on jäähdyttänyt tai lämmittänyt vähintään kaksi tuntia.");
        if (u.predicted)
          item(ul, "Ennuste: jos " + (cot ? "patterit eivät lämmitä" : "laite on pois päältä") +
            ", huone on " +
            u.predicted.hours + " tunnin päästä noin " +
            num(u.predicted.off_c, 1) + " °C.");
        if (!m.uses_outdoor)
          item(ul, "Ulkolämpötilaa ei ole tarpeeksi; malli käyttää vain huoneen omaa muutosta.");
      }
      if (u.validation) {
        var v = u.validation;
        item(ul, "Testipäivä " + fiDate(v.day) + ": keskivirhe " + num(v.mae_c, 2) +
          " °C, suurin " + num(v.max_error_c, 1) + " °C" +
          (v.passed ? " (hyväksytty)." : " (yli rajan)."),
          v.passed ? "rooms-ok" : "rooms-bad");
      } else if (m.ready) {
        item(ul, "Testipäivää ei voi vielä arvioida: tarvitaan kokonainen päivä ja sitä edeltävät lukemat.");
      }
      $body.appendChild(h);
      $body.appendChild(ul);
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
