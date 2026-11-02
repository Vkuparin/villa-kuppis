"""Finnish settings section of the web app (card F3.5).

Kept apart from pump_app's page so the two pages' sources do not collide;
``pump_app.render_page`` splices both pieces into the page. Plain ASCII
JS in raw strings: no Python escape can corrupt it.
"""

SECTION_HTML = r"""
  <section class="settings panel" id="settings-section" aria-label="Asetukset"
           data-fold="settings" data-fold-default="1">
    <div class="panel-head">
      <h2><span data-icon="gear"></span>Asetukset</h2>
      <div class="tools">
        <span id="set-status" role="status"></span>
        <select id="set-scope" aria-label="Asetusten kohde"
                title="Kohde tai huone, jonka asetuksia muokataan."></select>
      </div>
    </div>
    <p class="detail" id="set-intro">Huoneen asetus ohittaa kohteen asetuksen, ja kohteen
      asetus ohittaa oletuksen. Muutos tulee voimaan seuraavassa
      suunnitelmassa.</p>
    <div id="set-body"></div>
    <h3>Käyttöliittymä</h3>
    <p class="detail">Näkymä tallentuu tähän laitteeseen ja selaimeen.</p>
    <div class="set-row">
      <label for="ui-dark" class="set-name">Tumma teema</label>
      <button type="button" class="switch" role="switch" aria-checked="false"
              id="ui-dark"><span class="track"></span></button>
    </div>
    <div class="set-row">
      <label for="ui-accent" class="set-name">Aksenttiväri</label>
      <select id="ui-accent" aria-label="Aksenttiväri">
        <option value="blue">Sininen</option>
        <option value="green">Vihreä</option>
        <option value="purple">Purppura</option>
        <option value="pink">Pinkki</option>
        <option value="orange">Oranssi</option>
      </select>
    </div>
    <div class="set-row">
      <span class="set-label" title="Iso, vain luettava näkymä kodista, mökistä ja tulevista hinnoista seinälle kiinnitettävään tablettiin. Pitää näytön päällä ja päivittyy minuutin välein.">Seinänäkymä tabletille</span>
      <a class="set-link" href="wall">Avaa</a>
    </div>
  </section>
"""

STYLE = r"""
section.settings h3 { margin: 1.3rem 0 .2rem; font-size: .72rem; font-weight: 600; letter-spacing: .07em;
                      text-transform: uppercase; color: var(--text3); }
#set-body { max-width: 46rem; }
a.set-link { display: inline-flex; align-items: center; min-height: 44px; padding: 0 .8rem;
             border: 1px solid var(--line3); border-radius: 10px; background: var(--surface);
             color: var(--text); text-decoration: none; font-size: .95rem; }
a.set-link:hover { border-color: var(--ctl); }
.set-row { display: flex; flex-wrap: wrap; align-items: center; gap: .4rem .8rem;
           padding: .5rem 0; border-bottom: 1px solid var(--line2); max-width: 46rem; }
.set-label { flex: 1 1 14rem; cursor: help; }
.set-help { flex: 1 1 100%; margin: 0 0 .2rem; }
.set-help[hidden] { display: none; }
.set-src:empty { display: none; }
.set-reset { min-width: 40px; min-height: 40px; padding: 0; font-size: 1.1rem; color: var(--text3); }
.set-row input.set-num, .set-row input[type=time] {
  min-height: 44px; width: 7.5rem; font-size: .95rem; padding: 0 .7rem;
  border: 1px solid var(--line3); border-radius: 10px;
  background: var(--surface); color: var(--text); font-variant-numeric: tabular-nums; }
.set-row input[type=time] { width: 9.5rem; }
.set-row input[type=checkbox] { width: 1.6rem; height: 1.6rem; }
.set-src { font-size: .78rem; color: var(--text3); flex: 0 0 auto; padding: .1rem .5rem;
           border-radius: 999px; background: var(--surface2); }
#set-status { font-size: .82rem; color: var(--text3); }
#set-status.err { color: var(--err); }
#set-scope[hidden], #set-intro[hidden] { display: none; }
.set-name, .set-note { flex: 1 1 14rem; }
.set-saved { flex: 0 0 auto; font-size: .82rem; color: var(--ok); transition: opacity .6s; }
.set-saved.err { color: var(--err); flex-basis: 100%; }
.set-saved.gone { opacity: 0; }
"""

SCRIPT = r"""
(function () {
  "use strict";
  var $scope = document.getElementById("set-scope");
  var $body = document.getElementById("set-body");
  var $status = document.getElementById("set-status");
  var data = null;
  var SRC = { "default": "Oletus", site: "Kohteen asetus", room: "Huoneen asetus" };
  // The last save, shown next to its own row for a moment (errors stay).
  var saved = null;     // { key, text, err, at }
  var SAVED_MS = 3000;

  function say(text, isErr) {
    $status.textContent = text || "";
    $status.className = isErr ? "err" : "";
  }
  function unitLabel(d) { return d.unit ? " (" + d.unit + ")" : ""; }
  function fmtDefault(d) {
    var v = d.default;
    if (d.type === "bool") return v ? "päällä" : "pois";
    if (d.type === "choice") {
      for (var i = 0; i < d.choices.length; i++)
        if (d.choices[i].value === v) return d.choices[i].label;
    }
    return String(v).replace(".", ",") + (d.unit ? " " + d.unit : "");
  }
  function make(tag, props) {
    var el = document.createElement(tag);
    for (var k in props) el[k] = props[k];
    return el;
  }
  function controlFor(d, eff, id) {
    var v = eff.value, el;
    if (d.type === "bool") {
      el = make("button", { type: "button", id: id, className: "switch" });
      el.setAttribute("role", "switch");
      el.setAttribute("aria-checked", v ? "true" : "false");
      el.appendChild(make("span", { className: "track" }));
    } else if (d.type === "choice") {
      el = make("select", { id: id });
      d.choices.forEach(function (c) {
        var o = make("option", { value: c.value, textContent: c.label });
        o.selected = c.value === v;
        el.appendChild(o);
      });
    } else if (d.type === "time") {
      el = make("input", { type: "time", id: id, value: v });
    } else {
      // Text, not type=number, so the value reads "8,18" the Finnish way.
      // A field that takes a negative value keeps a keyboard with a minus.
      el = make("input", { type: "text", id: id, className: "set-num",
                           value: String(v).replace(".", ","), autocomplete: "off" });
      el.setAttribute("inputmode", d.min !== null && d.min < 0 ? "text" : "decimal");
    }
    return el;
  }
  function readControl(d, el) {
    if (d.type === "bool") return el.getAttribute("aria-checked") === "true";
    if (d.type === "number") {
      var text = el.value.trim().replace(",", ".").replace("\u2212", "-");
      return text === "" ? null : Number(text);     // NaN: not a number
    }
    return el.value;
  }
  function render() {
    if (!data) return;
    var scope = $scope.value;
    var eff = data.effective[scope];
    var own = data.overrides[scope] || {};
    $body.textContent = "";
    var group = null;
    // A value set at this very level needs no label; only inherited ones do.
    var level = scope.indexOf("room:") === 0 ? "room" : "site";
    var kind = null, site = null, alertRow = null;
    data.scopes.forEach(function (s) { if (s.id === scope) { kind = s.kind; site = s.site; } });
    data.definitions.forEach(function (d, i) {
      // Settings of the other site's control kind do nothing, so they are hidden,
      // as are settings that depend on a switch or choice that is off.
      if (d.kinds.indexOf(kind) < 0) return;
      if (d.site_only && level === "room") return;
      if (d.shown_if && d.shown_if.values.indexOf(eff[d.shown_if.key].value) < 0) return;
      if (d.group !== group) {
        group = d.group;
        $body.appendChild(make("h3", { textContent: group }));
      }
      var row = make("div", { className: "set-row" });
      var id = "set-" + d.key;
      // Not a <label for>: tapping the text must explain, not toggle the control.
      var label = make("span", { className: "set-label", textContent: d.label + unitLabel(d),
                                 title: d.help, tabIndex: 0 });
      label.setAttribute("role", "button");
      label.setAttribute("aria-expanded", "false");
      var help = make("p", { className: "detail set-help", textContent: d.help });
      help.hidden = true;
      var toggleHelp = function () {
        help.hidden = !help.hidden;
        label.setAttribute("aria-expanded", help.hidden ? "false" : "true");
      };
      label.addEventListener("click", toggleHelp);
      label.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggleHelp(); }
      });
      var ctl = controlFor(d, eff[d.key], id);
      ctl.title = "Oletus: " + fmtDefault(d);
      ctl.setAttribute("aria-label", d.label + unitLabel(d));
      var src = make("span", { className: "set-src",
        textContent: d.shared ? "Koti ja mökki"
          : eff[d.key].source === "default" || eff[d.key].source === level
          ? "" : SRC[eff[d.key].source] });
      // Saved when the control is released ("change"), not on every keystroke.
      ctl.addEventListener(d.type === "bool" ? "click" : "change", function () {
        if (d.type === "bool") {
          ctl.setAttribute("aria-checked", readControl(d, ctl) ? "false" : "true");
        }
        var v = readControl(d, ctl);
        if (v === null || (typeof v === "number" && isNaN(v))) {
          saved = { key: d.key, text: v === null ? "Anna arvo." : "Anna luku, esim. 8,5.",
                    err: true, at: Date.now() };
          render();
          return;
        }
        if (v === eff[d.key].value) return;
        send({ scope: scope, key: d.key, value: v });
      });
      row.appendChild(label); row.appendChild(ctl); row.appendChild(src);
      // A switch is its own reset: on or off.
      var mine = d.shared ? data.overrides.all || {} : own;
      if (d.type !== "bool" && Object.prototype.hasOwnProperty.call(mine, d.key)) {
        var reset = make("button", { type: "button", className: "set-reset",
                                     textContent: "\u21ba", title: "Palauta oletus" });
        reset.setAttribute("aria-label", "Palauta oletus");
        reset.addEventListener("click", function () {
          send({ scope: scope, key: d.key, reset: true });
        });
        row.appendChild(reset);
      }
      if (saved && saved.key === d.key && (saved.err || Date.now() - saved.at < SAVED_MS)) {
        var mark = make("span", { className: "set-saved" + (saved.err ? " err" : ""),
                                  textContent: saved.text });
        mark.setAttribute("role", "status");
        row.appendChild(mark);
        if (!saved.err) setTimeout(function () { mark.className += " gone"; },
                                   Math.max(0, SAVED_MS - (Date.now() - saved.at)));
      }
      row.appendChild(help);
      $body.appendChild(row);
      if (d.group === "Ilmoitukset") alertRow = row;
    });
    if (alertRow && level === "site") {
      var test = testRow(site);
      alertRow.after(test);
      if (site === "cottage") test.after(alarmRow());
    }
  }
  var hhmm = new Intl.DateTimeFormat("fi-FI", { timeZone: "Europe/Helsinki",
    hour: "2-digit", minute: "2-digit" });
  // The cottage's own alarm (integration 0.7.1+): it speaks up through the
  // cottage Home Assistant's phones when the home server goes silent.
  function alarmRow() {
    var row = make("div", { className: "set-row" });
    var note = make("span", { className: "set-note detail" });
    row.appendChild(note);
    row.hidden = true;
    fetch("api/cottage", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        var a = j && j.alarm;
        if (!j || !j.configured) return;
        note.textContent = !a ? "Mökin oma hälytys näkyy täällä, kun mökin integraatio on päivitetty."
          : !a.enabled ? "Mökin oma hälytys on pois päältä mökin Home Assistantin asetuksissa."
          : !a.phones.length ? "Mökin oma hälytys ei tavoita ketään: yhdistä puhelin mökin Home Assistantiin."
          : "Mökin oma hälytys on valmiina: jos kodin palvelin ei vastaa " + a.silent_minutes +
            " minuuttiin, mökki ilmoittaa itse mökin Home Assistantin puhelimiin (" +
            a.phones.join(", ") + ")." +
            (a.heard ? " Mökki kuuli kodin palvelimen viimeksi klo " + hhmm.format(new Date(a.heard)) + "." : "");
        row.hidden = false;
      })
      .catch(function () {});
    return row;
  }
  function testRow(site) {
    var row = make("div", { className: "set-row" });
    var hint = "Tarkista, että ilmoitukset tulevat puhelimeen.";
    var note = make("span", { className: "set-note detail", textContent: hint });
    note.setAttribute("role", "status");
    var btn = make("button", { type: "button", textContent: "Lähetä testi-ilmoitus" });
    btn.addEventListener("click", function () {
      btn.disabled = true;
      note.textContent = "Lähetetään…";
      fetch("api/alerts/test", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ site: site }) })
        .then(function (r) { return r.json().then(function (j) { return [r.ok, j]; }); })
        .then(function (res) {
          var j = res[1];
          note.textContent = !res[0] ? j.detail || "Lähetys epäonnistui."
            : "Testi-ilmoitus lähti kodin Home Assistantin kautta: " + (j.phones || []).join(", ") +
              "." + (j.failed && j.failed.length ? " Ei mennyt: " + j.failed.join(", ") + "." : "");
        })
        .catch(function () { note.textContent = "Lähetys epäonnistui."; })
        .then(function () { btn.disabled = false; });
    });
    row.appendChild(note); row.appendChild(btn);
    return row;
  }
  function load(keepScope) {
    return fetch("api/settings", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        data = j;
        var prev = keepScope ? $scope.value : null;
        $scope.textContent = "";
        // Each page shows its own site's settings (all when no page is known).
        var scopes = j.scopes.filter(function (s) {
          return typeof vkSite !== "function" || s.site === vkSite();
        });
        scopes.forEach(function (s) {
          $scope.appendChild(make("option", { value: s.id, textContent: s.label }));
        });
        // One scope (the cottage): nothing to choose and nothing overrides.
        $scope.hidden = scopes.length < 2;
        document.getElementById("set-intro").hidden = scopes.length < 2;
        if (prev) $scope.value = prev;
        render();
      })
      .catch(function () { say("Asetusten haku epäonnistui.", true); });
  }
  function send(body) {
    var mark = function (text, err) {
      saved = { key: body.key, text: text, err: err, at: Date.now() };
    };
    fetch("api/settings", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body) })
      .then(function (r) { return r.json().then(function (j) { return [r.ok, j]; }); })
      .then(function (res) {
        if (!res[0]) { mark("Tallennus epäonnistui: " + (res[1].detail || ""), true); render(); return; }
        mark("Tallennettu ✓", false);
        load(true);
      })
      .catch(function () { mark("Tallennus epäonnistui.", true); render(); });
  }
  $scope.addEventListener("change", render);
  document.addEventListener("vk-page", function () { load(false); });
  load(false);

  // Look and feel: per browser, applied at once (the page head applies it at load).
  var root = document.documentElement;
  var $dark = document.getElementById("ui-dark");
  var $accent = document.getElementById("ui-accent");
  function remember(key, value) {
    try { localStorage.setItem(key, value); } catch (e) {}
  }
  $dark.setAttribute("aria-checked",
    root.getAttribute("data-theme") === "dark" ? "true" : "false");
  $accent.value = root.getAttribute("data-accent") || "blue";
  $dark.addEventListener("click", function () {
    var dark = $dark.getAttribute("aria-checked") !== "true";
    $dark.setAttribute("aria-checked", dark ? "true" : "false");
    root.setAttribute("data-theme", dark ? "dark" : "light");
    remember("vk-theme", dark ? "dark" : "light");
  });
  $accent.addEventListener("change", function () {
    if ($accent.value === "blue") root.removeAttribute("data-accent");
    else root.setAttribute("data-accent", $accent.value);
    remember("vk-accent", $accent.value === "blue" ? "" : $accent.value);
  });
})();
"""
