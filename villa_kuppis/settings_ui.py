"""Finnish settings section of the web app (card F3.5).

Kept apart from pump_app's page so the two pages' sources do not collide;
``pump_app.render_page`` splices both pieces into the page. Plain ASCII
JS in raw strings: no Python escape can corrupt it.
"""

SECTION_HTML = r"""
  <section class="settings" aria-label="Asetukset">
    <h2>Asetukset</h2>
    <div class="hist-controls">
      <label>Kohde
        <select id="set-scope" aria-label="Asetusten kohde"></select>
      </label>
      <span id="set-status" role="status"></span>
    </div>
    <p class="detail">Huoneen asetus ohittaa kohteen asetuksen, ja kohteen
      asetus ohittaa oletuksen. Muutos tulee voimaan seuraavassa
      suunnitelmassa.</p>
    <div id="set-body"></div>
  </section>
"""

STYLE = r"""
section.settings { background: #fff; border: 1px solid #d9dee3;
                   border-radius: .75rem; padding: 1rem 1.1rem;
                   margin-bottom: 1rem; }
section.settings h2 { margin: 0 0 .6rem; font-size: 1.15rem; }
section.settings h3 { margin: 1rem 0 .3rem; font-size: 1rem; color: #44505c; }
.set-row { display: flex; flex-wrap: wrap; align-items: center; gap: .4rem .8rem;
           padding: .35rem 0; border-bottom: 1px solid #e3e8ec; }
.set-row label { flex: 1 1 14rem; }
.set-row input[type=number], .set-row input[type=time] {
  min-height: 44px; width: 7.5rem; font-size: 1rem; padding: 0 .5rem;
  border: 1px solid #9aa4ae; border-radius: .5rem; }
.set-row input[type=checkbox] { width: 1.6rem; height: 1.6rem; }
.set-src { font-size: .85rem; color: #55606b; flex: 0 0 9rem; }
#set-status { font-size: .85rem; color: #55606b; }
#set-status.err { color: #a11d1d; }
"""

SCRIPT = r"""
(function () {
  "use strict";
  var $scope = document.getElementById("set-scope");
  var $body = document.getElementById("set-body");
  var $status = document.getElementById("set-status");
  var data = null;
  var SRC = { "default": "Oletus", site: "Kohteen asetus", room: "Huoneen asetus" };

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
      el = make("input", { type: "checkbox", id: id, checked: !!v });
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
      el = make("input", { type: "number", id: id, step: "any",
                           value: String(v) });
      if (d.min !== null) el.min = d.min;
      if (d.max !== null) el.max = d.max;
    }
    return el;
  }
  function readControl(d, el) {
    if (d.type === "bool") return el.checked;
    if (d.type === "number") return el.value === "" ? null : Number(el.value);
    return el.value;
  }
  function render() {
    if (!data) return;
    var scope = $scope.value;
    var eff = data.effective[scope];
    var own = data.overrides[scope] || {};
    $body.textContent = "";
    var group = null;
    data.definitions.forEach(function (d, i) {
      if (d.group !== group) {
        group = d.group;
        $body.appendChild(make("h3", { textContent: group }));
      }
      var row = make("div", { className: "set-row" });
      var id = "set-" + d.key;
      var label = make("label", { htmlFor: id,
        textContent: d.label + unitLabel(d) + " – oletus " + fmtDefault(d) });
      var ctl = controlFor(d, eff[d.key], id);
      var src = make("span", { className: "set-src",
        textContent: SRC[eff[d.key].source] });
      // Saved when the control is released ("change"), not on every keystroke.
      ctl.addEventListener("change", function () {
        var v = readControl(d, ctl);
        if (v === null) {
          say("Anna arvo ennen tallennusta.", true);
          render();
          return;
        }
        if (v === eff[d.key].value) return;
        send({ scope: scope, key: d.key, value: v });
      });
      row.appendChild(label); row.appendChild(ctl); row.appendChild(src);
      if (Object.prototype.hasOwnProperty.call(own, d.key)) {
        var reset = make("button", { type: "button",
                                     textContent: "Palauta oletus" });
        reset.addEventListener("click", function () {
          send({ scope: scope, key: d.key, reset: true });
        });
        row.appendChild(reset);
      }
      $body.appendChild(row);
    });
  }
  function load(keepScope) {
    return fetch("/api/settings", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        data = j;
        var prev = keepScope ? $scope.value : null;
        $scope.textContent = "";
        j.scopes.forEach(function (s) {
          $scope.appendChild(make("option", { value: s.id, textContent: s.label }));
        });
        if (prev) $scope.value = prev;
        render();
      })
      .catch(function () { say("Asetusten haku epäonnistui.", true); });
  }
  function send(body) {
    say("Tallennetaan…");
    fetch("/api/settings", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body) })
      .then(function (r) { return r.json().then(function (j) { return [r.ok, j]; }); })
      .then(function (res) {
        if (!res[0]) { say("Tallennus epäonnistui: " + (res[1].detail || ""), true); return; }
        say("Tallennettu. Muutos tulee voimaan seuraavassa suunnitelmassa.");
        load(true);
      })
      .catch(function () { say("Tallennus epäonnistui.", true); });
  }
  $scope.addEventListener("change", render);
  load(false);
})();
"""
