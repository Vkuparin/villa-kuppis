"""Web page section for Home Assistant devices by area (devices.py)."""

SECTION_HTML = r"""
  <section class="devices" id="devices-section" aria-label="Laitteet" hidden>
    <div class="dev-head">
      <h2><span data-icon="bulb"></span>Laitteet</h2>
      <span id="dev-conn" class="badge" hidden></span>
      <button type="button" class="switch" role="switch" aria-checked="false" id="dev-edit"
        title="Järjestä huoneita ja laitteita nuolilla, piilota niitä ja palauta piilotettuja näkyviin. Oletus: pois.">
        <span class="track"></span><span>Järjestä</span></button>
    </div>
    <p id="dev-note" class="detail"></p>
    <div id="dev-areas" class="dev-areas"></div>
    <div class="panel dev-rules" id="dev-rules" hidden>
      <div class="panel-head">
        <h2 title="Villa Kuppiksen omat säännöt tämän kohteen laitteille. Ne toimivat aina, kun sääntö on käytössä; Automaatio-kytkin koskee vain jäähdytystä ja mökin lämmitystä."><span data-icon="clock"></span>Säännöt</h2>
        <div class="tools"><button type="button" id="dev-rule-add" class="primary">Uusi sääntö</button></div>
      </div>
      <p id="dev-rules-note" class="detail"></p>
      <div id="dev-rules-list"></div>
    </div>
    <details class="panel dev-autos" id="dev-autos" hidden>
      <summary title="Home Assistantissa tehdyt automaatiot. Täältä voit kytkeä niitä päälle ja pois; muokkaus tehdään Home Assistantissa.">
        <span data-icon="auto"></span>Home Assistantin automaatiot <span id="dev-autos-count" class="badge mute"></span></summary>
      <div id="dev-autos-list"></div>
    </details>
    <dialog id="dev-dialog" class="dev-dialog" aria-labelledby="dev-d-title">
      <form method="dialog" class="dev-d-head">
        <h3 id="dev-d-title"></h3>
        <button type="submit" aria-label="Sulje">&times;</button>
      </form>
      <div id="dev-d-body"></div>
    </dialog>
  </section>
"""

STYLE = r"""
section.devices { display: flex; flex-direction: column; gap: 1rem; }
section.devices[hidden] { display: none; }
.dev-head { display: flex; flex-wrap: wrap; align-items: center; gap: .5rem .8rem; }
.dev-head h2 { display: flex; align-items: center; gap: .55rem; margin: 0; font-size: 1.05rem; }
.dev-head h2 .ico { color: var(--text3); }
#dev-conn[hidden] { display: none; }
.dev-head #dev-edit { margin-left: auto; min-height: 40px; font-size: .88rem; color: var(--text2); }
#dev-note:empty { display: none; }
#dev-note:not(:empty) { margin: 0; padding: .6rem .8rem; border-radius: 10px;
                        background: var(--surface2); border-left: 3px solid var(--info); }
.dev-areas { display: flex; flex-direction: column; gap: 1.1rem; }
.dev-area { padding: .75rem .75rem .85rem; background: var(--surface2); border: 1px solid var(--line);
            border-radius: 18px; }
.dev-area-head { display: flex; flex-wrap: wrap; align-items: center; gap: .4rem .7rem;
                 margin: 0 0 .7rem; padding-left: .65rem; border-left: 4px solid var(--accent); }
.dev-area-head h3 { margin: 0; font-size: 1.05rem; letter-spacing: -.01em; }
.dev-area-head .floor { font-size: .78rem; color: var(--text3); font-weight: 500; margin-left: .35rem; }
.dev-area-head .tools { margin-left: auto; display: flex; flex-wrap: wrap; gap: .4rem; align-items: center; }
.dev-area-head .tools button { min-height: 36px; font-size: .84rem; }
.dev-area.hidden-area { border-style: dashed; }
.dev-area.hidden-area > .dev-grid, .dev-area.hidden-area h3 { opacity: .45; }
.dev-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(178px, 1fr)); gap: .75rem; }
.tile { position: relative; display: flex; flex-direction: column; gap: .3rem; min-height: 132px;
        padding: .8rem .85rem .75rem; background: var(--surface); border: 1px solid var(--line);
        border-radius: 14px; box-shadow: var(--shadow); min-width: 0; }
.tile.off-line { opacity: .6; }
.tile.hidden-tile { opacity: .45; border-style: dashed; }
.tile .t-top { display: flex; align-items: flex-start; gap: .55rem; min-width: 0; }
.tile .t-open { display: flex; flex-direction: column; align-items: flex-start; gap: .45rem; flex: 1;
                min-width: 0; min-height: 0; padding: 0; border: 0; background: none; text-align: left;
                color: inherit; cursor: pointer; }
.tile .t-ico { width: 34px; height: 34px; border-radius: 10px; display: grid; place-items: center; flex: none;
               background: var(--surface2); color: var(--text2); }
.tile.active .t-ico { background: var(--accent); color: #fff; }
.tile.kind-light.active .t-ico { background: var(--price); color: #1b1300; }
.tile.alert .t-ico { background: var(--err-bg); color: var(--err); }
.tile .t-name { font-size: .88rem; font-weight: 600; line-height: 1.25; color: var(--text);
                overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
.tile .t-new { margin-left: .3rem; font-size: .7rem; padding: .05rem .45rem; }
.tile .t-top button.switch { position: absolute; top: .75rem; right: .7rem; min-height: 34px; min-width: 0;
                             padding: 0; border: 0; background: none; }
.tile .t-top button.switch span:last-child { display: none; }
.tile .t-value { font-size: 1.35rem; font-weight: 650; font-variant-numeric: tabular-nums; line-height: 1.15;
                 margin-top: auto; }
.tile .t-value small { font-size: .8rem; font-weight: 500; color: var(--text3); margin-left: .15rem; }
.tile .t-sub { font-size: .78rem; color: var(--text3); min-height: 1.1em; }
.tile svg.spark { width: 100%; height: 26px; display: block; }
.tile svg.spark polyline { fill: none; stroke: var(--accent); stroke-width: 1.6; vector-effect: non-scaling-stroke; }
.tile.kind-temperature svg.spark polyline { stroke: var(--heat); }
.tile.kind-humidity svg.spark polyline { stroke: var(--cool); }
.tile input[type=range], .dev-dialog input[type=range] { width: 100%; accent-color: var(--accent); min-height: 28px; }
.tile .t-btns { display: flex; gap: .35rem; }
.tile .t-btns button { flex: 1; min-height: 36px; min-width: 0; padding: 0 .4rem; font-size: .84rem; }
.tile .t-msg { font-size: .76rem; color: var(--info); }
.tile .t-msg.err { color: var(--err); }
.tile .t-msg:empty { display: none; }
.tile .t-edit { display: flex; align-items: center; gap: .3rem; border-top: 1px solid var(--line2);
                padding-top: .4rem; margin-top: .15rem; }
.tile .t-edit button:not(.switch) { min-height: 34px; min-width: 34px; padding: 0; font-size: .9rem; }
.tile .t-edit button.switch { margin-left: auto; min-height: 34px; font-size: .78rem; color: var(--text2);
                              border: 0; background: none; padding: 0; }
.tile .t-edit button.switch span:last-child { display: none; }
.dev-rules[hidden] { display: none; }
#dev-rules-note:empty { display: none; }
.dev-rule { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; align-items: center;
            gap: .15rem .6rem; padding: .55rem 0; border-top: 1px solid var(--line2); }
.dev-rule .r-name { font-weight: 600; font-size: .92rem; }
.dev-rule .r-sum { grid-column: 1; font-size: .86rem; color: var(--text2); }
.dev-rule .r-stat { grid-column: 1; font-size: .78rem; color: var(--text3); }
.dev-rule .r-stat.err { color: var(--err); }
.dev-rule button.switch { grid-row: 1 / span 3; grid-column: 2; border: 0; background: none; min-height: 40px;
                          font-size: .82rem; color: var(--text2); }
.dev-rule .r-edit { grid-row: 1 / span 3; grid-column: 3; min-height: 38px; font-size: .84rem; }
.dev-rule.off .r-name, .dev-rule.off .r-sum { color: var(--text3); }
.rule-form label.fld select, .rule-form label.fld input { min-height: 44px; }
.rule-form input[type=time], .rule-form input[type=number] {
  min-height: 44px; padding: 0 .6rem; border: 1px solid var(--line3); border-radius: 10px;
  background: var(--surface); color: var(--text); font-size: .95rem; }
.rule-form [hidden] { display: none !important; }
.rule-form .r-off { display: inline-flex; align-items: center; gap: .4rem; flex: 0 1 150px; color: var(--text3);
                    font-size: .86rem; }
.rule-form .r-off input { width: 90px; }
.rule-form .r-line { display: flex; flex-wrap: wrap; gap: .5rem; align-items: center; }
.rule-form .r-line > * { flex: 1 1 140px; min-width: 0; }
.rule-form .r-line input[type=number] { flex: 0 1 110px; }
.rule-form fieldset { border: 1px solid var(--line2); border-radius: 12px; padding: .5rem .7rem .7rem; margin: .6rem 0; }
.rule-form legend { font-size: .82rem; color: var(--text3); padding: 0 .3rem; }
.rule-form .r-act { display: flex; flex-wrap: wrap; gap: .4rem; margin: .35rem 0; }
.rule-form .r-act > select { flex: 1 1 150px; min-width: 0; }
.rule-form .r-act input[type=number] { width: 90px; }
.rule-form .r-act button { min-width: 44px; }
.rule-form .r-buttons { display: flex; flex-wrap: wrap; gap: .5rem; margin-top: 1rem; }
.rule-form .r-buttons .danger { margin-left: auto; color: var(--err); }
.rule-form .r-msg { color: var(--err); font-size: .86rem; }
.rule-form .r-msg:empty { display: none; }
.rule-form .r-help { margin: -.3rem 0 .4rem; font-size: .8rem; }
.rule-form .r-grp { display: flex; flex-direction: column; gap: .3rem; margin: .7rem 0; font-size: .82rem; color: var(--text3); }
.rule-form .r-lab { font-size: .82rem; color: var(--text3); }
.rule-form select { min-height: 44px; }
.rule-form .r-chips { display: flex; flex-wrap: wrap; gap: .35rem; }
.rule-form .r-chips:empty { display: none; }
.rule-form .r-chip { display: inline-flex; align-items: center; gap: .2rem; padding: .15rem .2rem .15rem .7rem;
  border: 1px solid var(--line3); border-radius: 999px; font-size: .88rem; color: var(--text); background: var(--surface2, var(--surface)); }
.rule-form .r-chip button { min-width: 32px; min-height: 32px; padding: 0; border: 0; background: none; font-size: 1.1rem; color: var(--text3); }
.rule-form .r-days { display: flex; flex-wrap: wrap; gap: .3rem; }
.rule-form .r-days button { min-width: 42px; min-height: 40px; padding: 0 .4rem; border-radius: 10px; }
.rule-form .r-days button[aria-pressed=true] { background: var(--accent); border-color: var(--accent); color: var(--on-accent, #fff); }
.rule-form .r-list { display: flex; flex-direction: column; gap: .45rem; }
.rule-form .r-item { display: flex; flex-wrap: wrap; gap: .4rem; align-items: center; padding: .45rem;
  border: 1px solid var(--line2); border-radius: 10px; }
.rule-form .r-item > select { flex: 1 1 170px; min-width: 0; }
.rule-form .r-item > .r-sub { flex: 1 1 100%; min-width: 0; }
.rule-form .r-item > .r-sub:empty { display: none; }
.rule-form .r-item > .r-sub > select { width: 100%; }
.rule-form .r-act input[type=number] { flex: 0 1 100px; }
.rule-form .r-rm { min-width: 44px; min-height: 44px; padding: 0; margin-left: auto; order: 1; }
.rule-form .r-item > .r-sub, .rule-form .r-act > select[aria-label=Toiminto], .rule-form .r-act > input { order: 2; }
.rule-form .r-act > select[aria-label=Toiminto] { flex: 1 1 150px; }
.rule-form .r-add { align-self: flex-start; min-height: 40px; }
.rule-form .r-unit { flex: 0 1 auto !important; font-size: .86rem; color: var(--text3); }
.rule-form .r-extras { border: 1px solid var(--line2); border-radius: 12px; padding: .2rem .7rem; margin: .7rem 0; }
.rule-form .r-extras summary { min-height: 44px; display: flex; align-items: center; cursor: pointer; font-size: .9rem; color: var(--text2); }
.rule-form .r-extras[open] { padding-bottom: .7rem; }
.rule-form .r-extras > button.switch { margin: .4rem 0; }
.dev-dialog fieldset.d-pumps { border: 1px solid var(--line2); border-radius: 12px; padding: .3rem .8rem .5rem; margin: .7rem 0; }
.dev-dialog fieldset.d-pumps legend { font-size: .82rem; color: var(--text3); padding: 0 .3rem; }
.dev-autos { padding: .2rem 1.1rem; }
.dev-autos summary { display: flex; align-items: center; gap: .55rem; min-height: 48px; cursor: pointer;
                     font-weight: 600; font-size: .98rem; }
.dev-autos summary .ico { color: var(--text3); }
.dev-autos[hidden] { display: none; }
.dev-auto { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: .1rem .8rem;
            padding: .45rem 0; border-top: 1px solid var(--line2); }
.dev-auto .a-name { font-size: .9rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dev-auto .a-sub { grid-column: 1; font-size: .78rem; color: var(--text3); }
.dev-auto button.switch { grid-row: 1 / span 2; grid-column: 2; min-height: 40px; border: 0; background: none;
                          font-size: .82rem; color: var(--text2); }
.dev-dialog { width: min(560px, calc(100vw - 32px)); max-height: calc(100vh - 48px); padding: 1rem 1.1rem 1.2rem;
              border: 1px solid var(--line); border-radius: 16px; background: var(--surface); color: var(--text); }
.dev-dialog::backdrop { background: rgba(0, 0, 0, .45); }
.dev-d-head { display: flex; align-items: center; gap: .5rem; margin: 0 0 .6rem; }
.dev-d-head h3 { margin: 0; font-size: 1.05rem; flex: 1; }
.dev-d-head button { min-width: 40px; min-height: 40px; font-size: 1.3rem; padding: 0; }
.dev-dialog label.fld { display: flex; flex-direction: column; gap: .25rem; font-size: .82rem; color: var(--text3);
                        margin: .6rem 0; }
.dev-dialog input[type=text] { min-height: 44px; padding: 0 .7rem; border: 1px solid var(--line3);
                               border-radius: 10px; background: var(--surface); color: var(--text); font-size: .95rem; }
.dev-dialog .d-chart svg { width: 100%; height: 170px; display: block; }
.dev-dialog .d-chart .grid { stroke: var(--line2); }
.dev-dialog .d-chart text { fill: var(--text3); font-size: 10px; }
.dev-dialog .d-chart polyline { fill: none; stroke: var(--accent); stroke-width: 1.8; vector-effect: non-scaling-stroke; }
.dev-dialog .d-chart path.onoff { fill: var(--accent); fill-opacity: .25; stroke: var(--accent); stroke-width: 1.2;
                                  vector-effect: non-scaling-stroke; }
.dev-dialog .seg { margin: .5rem 0; }
.dev-dialog dl { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: .2rem .8rem; font-size: .84rem;
                 margin: .8rem 0 0; }
.dev-dialog dt { color: var(--text3); }
.dev-dialog dd { margin: 0; overflow-wrap: anywhere; }
.dev-dialog p.d-note { font-size: .82rem; color: var(--text3); margin: .4rem 0 0; }
.dev-dialog .d-row { display: flex; align-items: center; justify-content: space-between; gap: .8rem; margin: .5rem 0; }
@media (max-width: 560px) {
  .dev-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .6rem; }
  .tile { min-height: 120px; padding: .7rem; }
}
"""

SCRIPT = r"""
(function () {
  "use strict";
  var $section = document.getElementById("devices-section");
  var $areas = document.getElementById("dev-areas");
  var $note = document.getElementById("dev-note");
  var $conn = document.getElementById("dev-conn");
  var $edit = document.getElementById("dev-edit");
  var $autos = document.getElementById("dev-autos");
  var $autosList = document.getElementById("dev-autos-list");
  var $autosCount = document.getElementById("dev-autos-count");
  var $dialog = document.getElementById("dev-dialog");
  var $dTitle = document.getElementById("dev-d-title");
  var $dBody = document.getElementById("dev-d-body");
  var DATA = null, EDIT = false, BUSY = 0, OPEN_KEY = null, LOADING = false;
  var num1 = new Intl.NumberFormat("fi-FI", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  var num2 = new Intl.NumberFormat("fi-FI", { maximumFractionDigits: 2 });
  var clock = new Intl.DateTimeFormat("fi-FI", { timeZone: "Europe/Helsinki", hour: "2-digit", minute: "2-digit" });
  var dayClock = new Intl.DateTimeFormat("fi-FI", { timeZone: "Europe/Helsinki", day: "numeric",
    month: "numeric", hour: "2-digit", minute: "2-digit" });
  var ICONS = { temperature: "thermo", humidity: "drop", co2: "cloud", air: "wind", light: "bulb",
    plug: "plug", switch: "plug", fan: "fan", cover: "blind", power: "bolt", energy: "bolt",
    illuminance: "sun", battery: "battery", climate: "flame", scene: "play", script: "play",
    number: "dot", text: "dot", binary: "dot" };
  var BINARY = { door: ["Auki", "Kiinni"], garage_door: ["Auki", "Kiinni"], window: ["Auki", "Kiinni"],
    opening: ["Auki", "Kiinni"], motion: ["Liikettä", "Ei liikettä"], occupancy: ["Liikettä", "Ei liikettä"],
    presence: ["Paikalla", "Poissa"], moisture: ["Vuoto!", "Kuiva"], smoke: ["Savua!", "Ei savua"],
    gas: ["Kaasua!", "Ei kaasua"], battery: ["Heikko", "OK"], lock: ["Auki", "Lukittu"],
    problem: ["Ongelma", "OK"], connectivity: ["Yhteys", "Ei yhteyttä"] };
  var ALERT_CLASSES = ["moisture", "smoke", "gas", "problem", "battery"];
  var UNITS = { co2: "ppm" };
  var CONNECTION = { ok: null, stale: ["warn", "Home Assistant: tiedot vanhentuneet"],
    connecting: ["info", "Home Assistant: yhdistetään"], disconnected: ["err", "Home Assistant: ei yhteyttä"],
    auth_failed: ["err", "Home Assistant: tunnus hylätty"], stopped: ["err", "Home Assistant: ei yhteyttä"] };

  function el(tag, cls, text, title) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    if (title) e.title = title;
    return e;
  }
  function when(iso) {
    if (!iso) return "";
    var t = new Date(iso).getTime();
    if (isNaN(t)) return "";
    var s = Math.max(0, (Date.now() - t) / 1000);
    if (s < 60) return "juuri nyt";
    if (s < 3600) return Math.round(s / 60) + " min sitten";
    if (s < 86400) return Math.round(s / 3600) + " h sitten";
    return dayClock.format(new Date(t));
  }
  function post(path, body) {
    body.site = vkSite();
    return fetch("api/devices/" + path, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body) }).then(function (r) {
        return r.json().then(function (j) { return [r.ok, j]; });
      });
  }
  function switchBtn(on, label, title) {
    var b = el("button", "switch", null, title);
    b.type = "button";
    b.setAttribute("role", "switch");
    b.setAttribute("aria-checked", on ? "true" : "false");
    b.setAttribute("aria-label", label);
    b.appendChild(el("span", "track"));
    b.appendChild(el("span", "", on ? "Päällä" : "Pois"));
    return b;
  }
  var STATES = {good: "Hyvä", fair: "Tyydyttävä", moderate: "Kohtalainen", poor: "Huono",
    very_poor: "Erittäin huono", extremely_poor: "Erittäin huono", excellent: "Erinomainen",
    unknown: "Tuntematon", on: "Päällä", off: "Pois", open: "Auki", closed: "Kiinni",
    home: "Kotona", not_home: "Poissa", idle: "Lepotilassa", heat: "Lämmitys", cool: "Viilennys",
    detected: "Havaittu", clear: "Ei havaintoa", connected: "Yhdistetty", disconnected: "Ei yhteyttä",
    charging: "Latautuu", low: "Matala", normal: "Normaali", high: "Korkea",
    standby: "Valmiustila", heating: "Lämmittää", cooling: "Jäähdyttää", drying: "Kuivaa",
    dry: "Kuivaus", fan: "Puhallus", fan_only: "Puhallus", auto: "Automaattinen",
    heat_cool: "Automaattinen", none: "Ei mitään", ok: "Kunnossa", error: "Vika",
    true: "Kyllä", false: "Ei", available: "Saatavilla", unavailable: "Ei saatavilla",
    online: "Yhteys", offline: "Ei yhteyttä", active: "Aktiivinen", inactive: "Ei aktiivinen",
    locked: "Lukittu", unlocked: "Auki", playing: "Toistaa", paused: "Tauolla"};
  function stateWord(s) {
    var k = String(s).toLowerCase().replace(/ /g, "_");
    return STATES[k] || String(s);
  }
  function valueText(d) {
    if (!d.available) return ["Ei saatavilla", ""];
    var v = d.value, unit = d.unit || UNITS[d.kind] || "";
    switch (d.kind) {
      case "temperature": return [v === null ? "–" : num1.format(v), "°C"];
      case "humidity": case "battery": return [v === null ? "–" : Math.round(v), "%"];
      case "co2": case "power": case "illuminance": return [v === null ? "–" : Math.round(v), unit];
      case "air": case "energy": case "number": return [v === null ? "–" : num2.format(v), unit];
      case "text": return [stateWord(d.state), ""];
      case "binary":
        var words = BINARY[d.device_class] || ["Päällä", "Pois"];
        return [d.on ? words[0] : words[1], ""];
      case "light":
        return [d.on ? (d.brightness !== null && d.brightness !== undefined ? d.brightness : "Päällä") : "Pois",
                d.on && d.brightness !== null && d.brightness !== undefined ? "%" : ""];
      case "plug": case "switch": return [d.on ? "Päällä" : "Pois", ""];
      case "fan": return [d.on ? (d.percentage !== null && d.percentage !== undefined ? Math.round(d.percentage) : "Päällä") : "Pois",
                          d.on && d.percentage !== null && d.percentage !== undefined ? "%" : ""];
      case "cover": return [d.position !== null && d.position !== undefined ? Math.round(d.position) :
                            (d.state === "open" ? "Auki" : d.state === "closed" ? "Kiinni" : d.state),
                            d.position !== null && d.position !== undefined ? "% auki" : ""];
      case "climate": return [d.current !== null && d.current !== undefined ? num1.format(d.current) : "–", "°C"];
      case "scene": case "script": return ["", ""];
      default: return [stateWord(d.state), unit];
    }
  }
  function isActive(d) {
    if (!d.available) return false;
    if (d.kind === "binary") return !!d.on;
    if (d.kind === "cover") return d.state === "open" || d.state === "opening";
    return d.on === true;
  }
  function spark(values) {
    var lo = Math.min.apply(null, values), hi = Math.max.apply(null, values);
    if (hi - lo < 1e-9) { lo -= 1; hi += 1; }
    var pts = values.map(function (v, i) {
      return (i * 100 / (values.length - 1)).toFixed(1) + "," + (24 - (v - lo) / (hi - lo) * 22).toFixed(1);
    }).join(" ");
    var s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    s.setAttribute("class", "spark");
    s.setAttribute("viewBox", "0 0 100 26");
    s.setAttribute("preserveAspectRatio", "none");
    s.setAttribute("aria-hidden", "true");
    var p = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
    p.setAttribute("points", pts);
    s.appendChild(p);
    return s;
  }
  // Sends one action; the tile shows "Lähetetään…" until the next refresh.
  function act(d, action, value, msg) {
    msg.className = "t-msg";
    msg.textContent = "Lähetetään…";
    post("command", { key: d.key, action: action, value: value }).then(function (res) {
      if (!res[0] || res[1].state === "failed") {
        msg.className = "t-msg err";
        msg.textContent = res[0] ? "Muutos ei onnistunut." : (res[1].detail || "Muutos ei onnistunut.");
        msg.title = res[1].detail || "";
      } else {
        msg.textContent = "";
      }
      setTimeout(load, 600);
    }).catch(function () {
      msg.className = "t-msg err";
      msg.textContent = "Yhteys palveluun katkesi.";
    });
  }
  // A range input sends when released; refreshes wait while it is held.
  function slider(min, max, step, value, label, onSend) {
    var r = el("input");
    r.type = "range"; r.min = min; r.max = max; r.step = step;
    r.value = value; r.setAttribute("aria-label", label);
    r.addEventListener("pointerdown", function () { BUSY++; });
    r.addEventListener("change", function () { BUSY = Math.max(0, BUSY - 1); onSend(Number(r.value)); });
    r.addEventListener("blur", function () { BUSY = 0; });
    return r;
  }
  function tile(d, area, index, count) {
    var t = el("article", "tile kind-" + d.kind + (isActive(d) ? " active" : "") +
      (d.available ? "" : " off-line") + (d.hidden ? " hidden-tile" : ""));
    if (d.kind === "binary" && d.on && ALERT_CLASSES.indexOf(d.device_class) >= 0) t.className += " alert";
    var top = el("div", "t-top");
    var open = el("button", "t-open", null, "Näytä historia ja tiedot");
    open.type = "button";
    var ico = el("span", "t-ico");
    ico.innerHTML = vkIcon(ICONS[d.kind] || "dot");
    open.appendChild(ico);
    var nm = el("span", "t-name", d.name);
    open.appendChild(nm);
    if (d.new) nm.appendChild(el("span", "badge info t-new", "Uusi", "Uusi laite Home Assistantista."));
    open.addEventListener("click", function () { openDialog(d.key); });
    top.appendChild(open);
    var msg = el("div", "t-msg");
    var cmd = d.command;
    if (cmd && cmd.state === "pending") msg.textContent = "Lähetetään…";
    else if (cmd && (cmd.state === "failed" || cmd.state === "unknown")) {
      msg.className = "t-msg err";
      msg.textContent = cmd.state === "failed" ? "Muutos ei onnistunut." : "Laite ei vahvistanut muutosta.";
      msg.title = cmd.detail || "";
    }
    var caps = d.caps || {};
    if (caps.power && d.available) {
      var sw = switchBtn(!!d.on, d.name + " päällä");
      sw.disabled = cmd && cmd.state === "pending";
      sw.addEventListener("click", function () {
        var want = sw.getAttribute("aria-checked") !== "true";
        sw.setAttribute("aria-checked", want ? "true" : "false");
        act(d, "power", want, msg);
      });
      top.appendChild(sw);
    }
    t.appendChild(top);
    var vt = valueText(d);
    if (vt[0] !== "") {
      var v = el("div", "t-value", String(vt[0]));
      if (vt[1]) v.appendChild(el("small", "", vt[1]));
      t.appendChild(v);
    }
    var sub = "";
    if (d.kind === "climate" && d.setpoint !== null && d.setpoint !== undefined)
      sub = "Tavoite " + num1.format(d.setpoint) + " °C";
    else if ((d.kind === "scene" || d.kind === "script") && d.last_triggered)
      sub = "Viimeksi " + when(d.last_triggered);
    else if (d.changed && ["text", "scene", "script"].indexOf(d.kind) < 0)
      sub = (d.kind === "binary" || d.on !== null ? "Muuttui " : "") + when(d.changed);
    t.appendChild(el("div", "t-sub", sub, d.changed ? dayClock.format(new Date(d.changed)) : ""));
    if (d.spark && d.spark.length > 1) t.appendChild(spark(d.spark));
    if (d.available) {
      if (d.kind === "light" && caps.dim && d.on)
        t.appendChild(slider(1, 100, 1, d.brightness || 100, "Kirkkaus",
          function (val) { act(d, "brightness", val, msg); }));
      if (d.kind === "fan" && caps.speed && d.on)
        t.appendChild(slider(0, 100, caps.step || 1, d.percentage || 0, "Nopeus",
          function (val) { act(d, "speed", val, msg); }));
      if (d.kind === "cover") {
        var row = el("div", "t-btns");
        [["open", "Avaa"], ["stop", "Seis"], ["close", "Sulje"]].forEach(function (b) {
          if (!caps[b[0]]) return;
          var btn = el("button", "", b[1]);
          btn.type = "button";
          btn.addEventListener("click", function () { act(d, "cover", b[0], msg); });
          row.appendChild(btn);
        });
        if (row.children.length) t.appendChild(row);
      }
      if (d.kind === "climate" && caps.setpoint && d.on) {
        var crow = el("div", "t-btns");
        [[-1, "−"], [1, "+"]].forEach(function (b) {
          var btn = el("button", "", b[1], b[0] < 0 ? "Laske tavoitetta" : "Nosta tavoitetta");
          btn.type = "button";
          btn.addEventListener("click", function () {
            var next = (d.setpoint || 20) + b[0] * (caps.step || 0.5);
            d.setpoint = next;
            act(d, "setpoint", next, msg);
          });
          crow.appendChild(btn);
        });
        t.appendChild(crow);
      }
      if (d.kind === "scene" || d.kind === "script") {
        var run = el("button", "", "Käynnistä");
        run.type = "button";
        run.addEventListener("click", function () { act(d, "run", true, msg); });
        var rrow = el("div", "t-btns");
        rrow.appendChild(run);
        t.appendChild(rrow);
      }
    }
    t.appendChild(msg);
    if (EDIT) {
      var ed = el("div", "t-edit");
      [[-1, "←", "Siirrä aiemmaksi"], [1, "→", "Siirrä myöhemmäksi"]].forEach(function (m) {
        var b = el("button", "", m[1], m[2]);
        b.type = "button";
        b.setAttribute("aria-label", m[2]);
        b.disabled = m[0] < 0 ? index === 0 : index === count - 1;
        b.addEventListener("click", function () { move(area, index, m[0]); });
        ed.appendChild(b);
      });
      var vis = switchBtn(!d.hidden, d.name + " näkyvissä", "Näkyykö laite sivulla. Oletus: näkyy.");
      vis.lastChild.textContent = "Näkyy";
      vis.addEventListener("click", function () {
        post("layout", { key: d.key, hidden: !d.hidden }).then(load);
      });
      ed.appendChild(vis);
      t.appendChild(ed);
    }
    return t;
  }
  function move(area, index, dir) {
    var keys = area.devices.map(function (d) { return d.key; });
    var j = index + dir;
    if (j < 0 || j >= keys.length) return;
    var k = keys[index]; keys[index] = keys[j]; keys[j] = k;
    post("layout", { order: keys }).then(load);
  }
  function moveArea(index, dir) {
    var ids = DATA.areas.map(function (a) { return a.id; });
    var j = index + dir;
    if (j < 0 || j >= ids.length) return;
    var k = ids[index]; ids[index] = ids[j]; ids[j] = k;
    post("layout", { areas: ids }).then(load);
  }
  function areaBlock(area, index) {
    var devices = EDIT ? area.devices : area.devices.filter(function (d) { return !d.hidden; });
    if (!EDIT && (area.hidden || !devices.length)) return null;
    var box = el("div", "dev-area" + (area.hidden ? " hidden-area" : ""));
    var head = el("div", "dev-area-head");
    var h = el("h3", "", area.name);
    if (area.floor) h.appendChild(el("span", "floor", area.floor));
    head.appendChild(h);
    var tools = el("div", "tools");
    var lightsOn = area.devices.some(function (d) { return d.kind === "light" && d.on; });
    if (lightsOn && !EDIT) {
      var off = el("button", "", "Valot pois", "Sammuttaa kaikki huoneen valot Home Assistantissa.");
      off.type = "button";
      off.addEventListener("click", function () {
        off.disabled = true;
        post("lights_off", { area: area.id }).then(function () { setTimeout(load, 600); });
      });
      tools.appendChild(off);
    }
    if (EDIT) {
      [[-1, "↑", "Siirrä huone ylemmäs"], [1, "↓", "Siirrä huone alemmas"]].forEach(function (m) {
        var b = el("button", "", m[1], m[2]);
        b.type = "button";
        b.setAttribute("aria-label", m[2]);
        b.disabled = m[0] < 0 ? index === 0 : index === DATA.areas.length - 1;
        b.addEventListener("click", function () { moveArea(index, m[0]); });
        tools.appendChild(b);
      });
      var vis = switchBtn(!area.hidden, area.name + " näkyvissä", "Näkyykö huone sivulla. Oletus: näkyy.");
      vis.lastChild.textContent = "Näkyy";
      vis.addEventListener("click", function () {
        post("layout", { area: area.id, area_hidden: !area.hidden }).then(load);
      });
      tools.appendChild(vis);
    }
    head.appendChild(tools);
    box.appendChild(head);
    var grid = el("div", "dev-grid");
    devices.forEach(function (d, i) { grid.appendChild(tile(d, area, area.devices.indexOf(d), area.devices.length)); });
    if (!devices.length) grid.appendChild(el("p", "detail", "Ei laitteita."));
    box.appendChild(grid);
    return box;
  }
  function renderAutos(list) {
    $autos.hidden = !list.length;
    $autosCount.textContent = list.length ? String(list.length) : "";
    $autosList.textContent = "";
    list.forEach(function (a) {
      var row = el("div", "dev-auto");
      row.appendChild(el("span", "a-name", a.name, a.entity_id));
      row.appendChild(el("span", "a-sub", a.last_triggered ? "Viimeksi " + when(a.last_triggered) : "Ei ajettu"));
      var sw = switchBtn(a.on, a.name + " päällä", "Kytkee automaation päälle tai pois Home Assistantissa.");
      sw.disabled = !a.available || (a.command && a.command.state === "pending");
      var msg = el("span", "t-msg");
      sw.addEventListener("click", function () {
        var want = sw.getAttribute("aria-checked") !== "true";
        sw.setAttribute("aria-checked", want ? "true" : "false");
        sw.lastChild.textContent = want ? "Päällä" : "Pois";
        act(a, "power", want, msg);
      });
      row.appendChild(sw);
      row.appendChild(msg);
      $autosList.appendChild(row);
    });
  }
  // --- rules (rules.py) ---
  var $rules = document.getElementById("dev-rules");
  var $rulesList = document.getElementById("dev-rules-list");
  var $rulesNote = document.getElementById("dev-rules-note");
  var KIND_NAMES = { between: "Päällä aikavälillä", event: "Kun tapahtuu…", level: "Anturin mukaan",
    cheapest: "Halvimmat tunnit" };
  var KIND_HELP = {
    between: "Laitteet päällä alusta loppuun, muuten pois. Esim. jouluvalot auringonlaskusta klo 23:een.",
    event: "Kun jokin tapahtuu (kellonaika, anturin raja, laite päälle tai pois, hinta, kotiintulo), tehdään valitut toiminnot.",
    level: "Laitteet päälle, kun anturin lukema alittaa tai ylittää rajan, ja pois, kun se palaa. Esim. lämmitin, tuuletin tai ilmankuivain.",
    cheapest: "Laitteet päällä sähkön halvimpina 15 minuutin jaksoina päivittäisen ikkunan aikana." };
  var TRIGGER_NAMES = { time: "Tiettynä hetkenä", above: "Arvo nousee yli", below: "Arvo laskee alle",
    turns_on: "Laite tai anturi menee päälle", turns_off: "Laite tai anturi menee pois",
    price_below: "Hinta laskee alle", price_above: "Hinta nousee yli",
    arrive: "Joku tulee kotiin", leave: "Viimeinen lähtee" };
  var COND_NAMES = { above: "Arvo yli", below: "Arvo alle", is_on: "Laite päällä", is_off: "Laite pois",
    home: "Joku on kotona", away: "Kukaan ei ole kotona" };
  var DAY_NAMES = ["ma", "ti", "ke", "to", "pe", "la", "su"];
  // Action choices by device: [value, label, needs a number, min, max, step].
  function actionChoices(d) {
    var c = d ? d.caps || {} : {}, out = [];
    if (!d) return out;
    if (d.kind === "scene" || d.kind === "script") return [["run", "Käynnistä"]];
    if (c.power) { out.push(["on", "Päälle"]); out.push(["off", "Pois"]); }
    if (c.dim) out.push(["brightness", "Kirkkaus %", true, 1, 100, 1]);
    if (c.ct) out.push(["color_temp", "Värilämpötila K", true, c.ct_min || 2000, c.ct_max || 6500, 50]);
    if (c.open) out.push(["open", "Avaa"]);
    if (c.close) out.push(["close", "Sulje"]);
    if (c.stop) out.push(["stop", "Pysäytä"]);
    if (c.position) out.push(["position", "Asento %", true, 0, 100, 1]);
    if (c.speed) out.push(["speed", "Nopeus %", true, 0, 100, c.step || 1]);
    if (c.setpoint) out.push(["setpoint", "Tavoite °C", true, c.min || 5, c.max || 35, c.step || 0.5]);
    return out;
  }
  function allDevices() {
    var out = [];
    (DATA ? DATA.areas : []).forEach(function (a) {
      a.devices.forEach(function (d) { out.push({ d: d, area: a.name }); });
    });
    (DATA ? DATA.automations : []).forEach(function (a) {
      out.push({ d: { key: a.key, name: a.name, kind: "automation", caps: { power: true } }, area: "Automaatiot" });
    });
    return out;
  }
  function devByKey(key) {
    var f = allDevices().filter(function (x) { return x.d.key === key; })[0];
    return f ? f.d : null;
  }
  function devName(key) {
    var d = devByKey(key);
    return d ? d.name : "(puuttuva laite)";
  }
  function fi(n) { return String(n).replace(".", ","); }
  function names(keys) {
    var n = (keys || []).map(devName);
    return n.length < 2 ? n.join("") : n.slice(0, -1).join(", ") + " ja " + n[n.length - 1];
  }
  function hm(minutes) {
    var m = Math.abs(minutes), h = Math.floor(m / 60), r = m % 60;
    return (h ? h + " h" : "") + (h && r ? " " : "") + (r ? r + " min" : "");
  }
  function momentText(m) {
    if (!m) return "";
    if (m.type === "time") return "klo " + m.at;
    var ev = m.event === "sunset" ? "auringonlasku" : "auringonnousu";
    var evIn = m.event === "sunset" ? "auringonlaskua" : "auringonnousua";
    var evOf = m.event === "sunset" ? "auringonlaskun" : "auringonnousun";
    if (!m.offset) return ev;
    return m.offset < 0 ? hm(m.offset) + " ennen " + evIn : hm(m.offset) + " " + evOf + " jälkeen";
  }
  function daysText(days) {
    if (!days || !days.length || days.length === 7) return "";
    var s = days.join(",");
    if (s === "0,1,2,3,4") return "arkisin";
    if (s === "5,6") return "viikonloppuisin";
    return days.map(function (d) { return DAY_NAMES[d]; }).join(", ");
  }
  function actionText(a) {
    var v = a.value, what = {
      power: v ? "päälle" : "pois", brightness: "kirkkaus " + v + " %", color_temp: v + " K",
      position: "asento " + v + " %", speed: "nopeus " + v + " %", setpoint: "tavoite " + fi(v) + " °C",
      run: "käynnistä", cover: v === "open" ? "auki" : v === "close" ? "kiinni" : "pysäytä" }[a.action];
    return devName(a.key) + " " + (what || a.action);
  }
  function triggerText(t) {
    if (t.type === "time" || t.type === "sun") return momentText(t);
    if (t.type === "above" || t.type === "below")
      return devName(t.key) + (t.type === "above" ? " yli " : " alle ") + fi(t.value);
    if (t.type === "turns_on" || t.type === "turns_off")
      return devName(t.key) + (t.type === "turns_on" ? " päälle" : " pois");
    if (t.type === "price_below" || t.type === "price_above")
      return "hinta " + (t.type === "price_below" ? "alle " : "yli ") + fi(t.value) + " snt";
    return t.type === "arrive" ? "joku tulee kotiin" : "viimeinen lähtee";
  }
  function condText(c) {
    if (c.type === "above" || c.type === "below") return devName(c.key) + (c.type === "above" ? " yli " : " alle ") + fi(c.value);
    if (c.type === "is_on" || c.type === "is_off") return devName(c.key) + (c.type === "is_on" ? " päällä" : " pois");
    return c.type === "home" ? "joku kotona" : "kukaan ei kotona";
  }
  function extrasText(r) {
    var parts = [];
    var d = daysText(r.days);
    if (d) parts.push(d);
    if (r.only) parts.push("vain klo " + r.only.start + "–" + r.only.end);
    if (r.max_price !== undefined && r.max_price !== null) parts.push("hinta enintään " + fi(r.max_price) + " snt/kWh");
    (r.when || []).forEach(function (c) { parts.push("kun " + condText(c)); });
    return parts.length ? " (" + parts.join(", ") + ")" : "";
  }
  function ruleText(r) {
    var s;
    if (r.kind === "between") s = names(r.keys) + " päällä " + momentText(r.start) + " – " + momentText(r.end);
    else if (r.kind === "cheapest")
      s = names(r.keys) + " päällä " + fi(r.hours) + " h halvimpina jaksoina klo " + r.start + "–" + r.end;
    else if (r.kind === "level")
      s = names(r.keys) + " päällä, kun " + devName(r.sensor) + (r.mode === "below" ? " alle " : " yli ") + fi(r.on_at) +
        ", pois " + (r.mode === "below" ? "yli " : "alle ") + fi(r.off_at);
    else {
      s = "Kun " + r.triggers.map(triggerText).join(" tai ") + ": " + r.actions.map(actionText).join(", ");
      if (r.off_after) s += "; pois " + hm(r.off_after) + " kuluttua";
    }
    return s + extrasText(r);
  }
  function periodsText(list) {
    if (!list || !list.length) return "";
    var spans = [], start = null, prev = null;
    list.forEach(function (iso) {
      var t = new Date(iso).getTime();
      if (prev !== null && t === prev + 900000) { prev = t; return; }
      if (start !== null) spans.push([start, prev + 900000]);
      start = t; prev = t;
    });
    spans.push([start, prev + 900000]);
    return spans.map(function (s) { return clock.format(new Date(s[0])) + "–" + clock.format(new Date(s[1])); }).join(", ");
  }
  var RESULT = { confirmed: "tehty", pending: "lähetetty", failed: "epäonnistui", unknown: "ei vahvistusta" };
  function renderRules(list) {
    $rules.hidden = !DATA.registry;
    $rulesList.textContent = "";
    if (!list.length) $rulesList.appendChild(el("p", "detail",
      "Ei sääntöjä. Esimerkiksi pistokkeet päälle arkiaamuisin, jouluvalot auringonlaskusta klo 23:een, " +
      "lämmitin anturin mukaan tai laite päälle sähkön halvimpina tunteina."));
    list.forEach(function (r) {
      var row = el("div", "dev-rule" + (r.enabled ? "" : " off"));
      if (r.rule.name) row.appendChild(el("span", "r-name", r.rule.name));
      row.appendChild(el("span", r.rule.name ? "r-sum" : "r-name", ruleText(r.rule)));
      var st = r.status || {}, parts = [];
      if (r.rule.kind === "cheapest" && st.periods && st.periods.length) parts.push("Jaksot: " + periodsText(st.periods));
      if (r.rule.kind === "level" && st.reading !== undefined && st.reading !== null) parts.push("Nyt " + fi(st.reading));
      if (st.now_on !== undefined && st.now_on !== null && r.rule.kind !== "cheapest") parts.push(st.now_on ? "Nyt päällä" : "Nyt pois");
      if (st.off_due) parts.push("Pois klo " + clock.format(new Date(st.off_due)));
      if (r.last_run) parts.push("Viimeksi " + when(r.last_run) + ": " + (RESULT[r.last_result] || r.last_result));
      var stat = el("span", "r-stat" + (st.problem || r.last_result === "failed" ? " err" : ""),
        st.problem || parts.join(" · "), r.last_detail || "");
      row.appendChild(stat);
      var sw = switchBtn(r.enabled, (r.rule.name || "Sääntö") + " käytössä", "Onko sääntö käytössä. Oletus: käytössä.");
      sw.addEventListener("click", function () {
        sw.disabled = true;
        post("rules", { id: r.id, enabled: !r.enabled }).then(load);
      });
      row.appendChild(sw);
      var ed = el("button", "r-edit", "Muokkaa");
      ed.type = "button";
      ed.addEventListener("click", function () { editRule(r); });
      row.appendChild(ed);
      $rulesList.appendChild(row);
    });
  }
  function select(options, value, label) {
    var s = el("select");
    s.setAttribute("aria-label", label);
    options.forEach(function (o) {
      var opt = el("option", "", o[1]);
      opt.value = o[0];
      if (String(o[0]) === String(value)) opt.selected = true;
      s.appendChild(opt);
    });
    return s;
  }
  function field(label, control, help) {
    var l = el("label", "fld", label, help);
    l.appendChild(control);
    return l;
  }
  function group(label, control, help) {
    // A labelled block for controls that are not one form input.
    var g = el("div", "fld r-grp");
    g.appendChild(el("span", "r-lab", label, help));
    g.appendChild(control);
    return g;
  }
  function numInput(value, label, step) {
    var v = el("input"); v.type = "number"; v.step = step || "any";
    v.value = value !== undefined && value !== null ? value : "";
    v.setAttribute("aria-label", label);
    return v;
  }
  function deviceSelect(filter, value, label) {
    var opts = allDevices().filter(function (x) { return filter(x.d); })
      .map(function (x) { return [x.d.key, x.d.name + " (" + x.area + ")"]; });
    if (value && !opts.some(function (o) { return o[0] === value; })) opts.unshift([value, "(puuttuva laite)"]);
    if (!value) opts.unshift(["", "Valitse…"]);
    return select(opts, value || "", label);
  }
  function powerable(d) { return d.caps && d.caps.power; }
  function numeric(d) {
    return ["temperature", "humidity", "co2", "air", "power", "energy", "illuminance", "battery", "number", "climate"]
      .indexOf(d.kind) >= 0;
  }
  function onOff(d) { return d.kind === "binary" || powerable(d); }
  function actionable(d) { return actionChoices(d).length > 0; }
  function devicePicker(keys, filter) {
    // Chips for the chosen devices and one list to add more.
    var box = el("div", "r-picks"), chosen = (keys || []).slice();
    var chips = el("div", "r-chips");
    var add = null;
    function draw() {
      chips.textContent = "";
      chosen.forEach(function (k) {
        var chip = el("span", "r-chip");
        chip.appendChild(el("span", "", devName(k)));
        var x = el("button", "", "×");
        x.type = "button";
        x.setAttribute("aria-label", "Poista " + devName(k));
        x.addEventListener("click", function () { chosen.splice(chosen.indexOf(k), 1); draw(); });
        chip.appendChild(x);
        chips.appendChild(chip);
      });
      var opts = allDevices().filter(function (x) { return filter(x.d) && chosen.indexOf(x.d.key) < 0; })
        .map(function (x) { return [x.d.key, x.d.name + " (" + x.area + ")"]; });
      var fresh = select([["", chosen.length ? "Lisää laite…" : "Valitse laite…"]].concat(opts), "", "Lisää laite");
      fresh.hidden = !opts.length || chosen.length >= 10;
      fresh.addEventListener("change", function () {
        if (fresh.value) { chosen.push(fresh.value); draw(); }
      });
      if (add) add.replaceWith(fresh); else box.appendChild(fresh);
      add = fresh;
    }
    box.appendChild(chips);
    draw();
    box.read = function () { return chosen.slice(); };
    return box;
  }
  function daysPicker(days) {
    var box = el("div", "r-days");
    box.setAttribute("role", "group");
    box.setAttribute("aria-label", "Viikonpäivät");
    var on = days && days.length ? days.slice() : [0, 1, 2, 3, 4, 5, 6];
    var buttons = DAY_NAMES.map(function (n, i) {
      var b = el("button", "", n);
      b.type = "button";
      b.setAttribute("aria-pressed", on.indexOf(i) >= 0 ? "true" : "false");
      b.addEventListener("click", function () {
        b.setAttribute("aria-pressed", b.getAttribute("aria-pressed") === "true" ? "false" : "true");
      });
      box.appendChild(b);
      return b;
    });
    box.read = function () {
      var out = [];
      buttons.forEach(function (b, i) { if (b.getAttribute("aria-pressed") === "true") out.push(i); });
      return out;
    };
    return box;
  }
  function momentEditor(m, label) {
    m = m || { type: "time", at: "18:00" };
    var box = el("div", "r-line");
    var type = select([["time", "Kellonaika"], ["sunset", "Auringonlasku"], ["sunrise", "Auringonnousu"]],
      m.type === "time" ? "time" : m.event, label);
    var time = el("input"); time.type = "time"; time.value = m.at || "18:00"; time.setAttribute("aria-label", label + ": kellonaika");
    var off = el("input"); off.type = "number"; off.step = 5; off.min = -240; off.max = 240;
    off.value = m.offset || 0;
    off.setAttribute("aria-label", label + ": siirto minuutteina");
    var offBox = el("span", "r-off", null, "Siirto minuutteina: miinus on ennen, plus jälkeen. Oletus 0.");
    offBox.appendChild(off);
    offBox.appendChild(el("span", "", "min"));
    function show() { time.hidden = type.value !== "time"; offBox.hidden = type.value === "time"; }
    type.addEventListener("change", show);
    show();
    box.appendChild(type); box.appendChild(time); box.appendChild(offBox);
    box.read = function () {
      return type.value === "time" ? { type: "time", at: time.value }
        : { type: "sun", event: type.value, offset: Number(off.value) || 0 };
    };
    return box;
  }
  function removeBtn(label, onClick) {
    var rm = el("button", "r-rm", "×", label);
    rm.type = "button";
    rm.setAttribute("aria-label", label);
    rm.addEventListener("click", onClick);
    return rm;
  }
  function listEditor(box, items, make, addLabel, max, min) {
    min = min === undefined ? 1 : min;
    // Rows made by make(item) -> element with .read(); an add button below.
    var rows = [];
    var addBtn = el("button", "r-add", addLabel);
    addBtn.type = "button";
    function add(item) {
      var row = make(item);
      var rm = removeBtn("Poista", function () {
        if (rows.length <= min) return;
        row.remove(); rows.splice(rows.indexOf(row), 1); sync();
      });
      row.appendChild(rm);
      box.insertBefore(row, addBtn);
      rows.push(row);
      sync();
    }
    function sync() {
      addBtn.hidden = rows.length >= max;
      rows.forEach(function (r) { r.lastChild.hidden = rows.length <= min; });
    }
    addBtn.addEventListener("click", function () { add(null); });
    box.appendChild(addBtn);
    (items && items.length ? items : min ? [null] : []).forEach(add);
    sync();
    return function () { return rows.map(function (r) { return r.read(); }); };
  }
  function triggerRow(t) {
    t = t || { type: "time", at: "07:00" };
    var line = el("div", "r-item");
    var trig = select(Object.keys(TRIGGER_NAMES).filter(function (x) {
      return (x !== "arrive" && x !== "leave") || DATA.people;
    }).map(function (x) { return [x, TRIGGER_NAMES[x]]; }), t.type === "sun" ? "time" : t.type, "Kun");
    var tbox = el("div", "r-sub");
    var tread = null;
    function build() {
      tbox.textContent = "";
      var tt = trig.value;
      if (tt === "time") {
        var m = momentEditor(t.type === "time" || t.type === "sun" ? t : { type: "time", at: "07:00" }, "Hetki");
        tbox.appendChild(m);
        tread = m.read;
      } else if (tt === "above" || tt === "below") {
        var sd = deviceSelect(numeric, t.key, "Anturi");
        var v = numInput(t.value !== undefined ? t.value : 20, "Raja");
        var l = el("div", "r-line"); l.appendChild(sd); l.appendChild(v);
        tbox.appendChild(l);
        tread = function () { return { type: tt, key: sd.value, value: Number(v.value) }; };
      } else if (tt === "price_below" || tt === "price_above") {
        var pv = numInput(t.value !== undefined ? t.value : 5, "Hinta snt/kWh");
        var pl = el("div", "r-line"); pl.appendChild(pv); pl.appendChild(el("span", "r-unit", "snt/kWh"));
        tbox.appendChild(pl);
        tread = function () { return { type: tt, value: Number(pv.value) }; };
      } else if (tt === "turns_on" || tt === "turns_off") {
        var od = deviceSelect(onOff, t.key, "Laite");
        tbox.appendChild(od);
        tread = function () { return { type: tt, key: od.value }; };
      } else {
        tread = function () { return { type: tt }; };
      }
    }
    trig.addEventListener("change", function () { t = { type: trig.value }; build(); });
    build();
    line.appendChild(trig); line.appendChild(tbox);
    line.read = function () { return tread(); };
    return line;
  }
  function actionRow(a) {
    a = a || { key: "", action: "power", value: true };
    var line = el("div", "r-item r-act");
    var dsel = deviceSelect(actionable, a.key, "Laite");
    var cur = a.action === "power" ? (a.value ? "on" : "off") : a.action === "cover" ? a.value : a.action;
    var asel = el("select"); asel.setAttribute("aria-label", "Toiminto");
    var val = numInput(a.action !== "power" && typeof a.value === "number" ? a.value : "", "Arvo");
    function fill() {
      var choices = actionChoices(devByKey(dsel.value));
      if (!choices.length) choices = [["on", "Päälle"], ["off", "Pois"]];
      asel.textContent = "";
      choices.forEach(function (c) {
        var o = el("option", "", c[1]); o.value = c[0];
        if (c[0] === cur) o.selected = true;
        asel.appendChild(o);
      });
      showVal();
    }
    function showVal() {
      var c = actionChoices(devByKey(dsel.value)).filter(function (x) { return x[0] === asel.value; })[0];
      val.hidden = !(c && c[2]);
      if (c && c[2]) {
        val.min = c[3]; val.max = c[4]; val.step = c[5];
        if (val.value === "" || Number(val.value) < c[3] || Number(val.value) > c[4])
          val.value = asel.value === "color_temp" ? 2700 : asel.value === "setpoint" ? 21 : 50;
      }
    }
    dsel.addEventListener("change", function () { cur = null; fill(); });
    asel.addEventListener("change", function () { cur = asel.value; showVal(); });
    fill();
    line.appendChild(dsel); line.appendChild(asel); line.appendChild(val);
    line.read = function () {
      var act = asel.value;
      if (act === "on" || act === "off") return { key: dsel.value, action: "power", value: act === "on" };
      if (act === "open" || act === "close" || act === "stop") return { key: dsel.value, action: "cover", value: act };
      if (act === "run") return { key: dsel.value, action: "run", value: true };
      return { key: dsel.value, action: act, value: Number(val.value) };
    };
    return line;
  }
  function condRow(c) {
    c = c || { type: "above", key: "", value: 20 };
    var line = el("div", "r-item");
    var type = select(Object.keys(COND_NAMES).filter(function (x) {
      return (x !== "home" && x !== "away") || DATA.people;
    }).map(function (x) { return [x, COND_NAMES[x]]; }), c.type, "Ehto");
    var sub = el("div", "r-sub");
    var cread = null;
    function build() {
      sub.textContent = "";
      var t = type.value;
      if (t === "above" || t === "below") {
        var sd = deviceSelect(numeric, c.key, "Anturi");
        var v = numInput(c.value !== undefined ? c.value : 20, "Raja");
        var l = el("div", "r-line"); l.appendChild(sd); l.appendChild(v);
        sub.appendChild(l);
        cread = function () { return { type: t, key: sd.value, value: Number(v.value) }; };
      } else if (t === "is_on" || t === "is_off") {
        var od = deviceSelect(onOff, c.key, "Laite");
        sub.appendChild(od);
        cread = function () { return { type: t, key: od.value }; };
      } else {
        cread = function () { return { type: t }; };
      }
    }
    type.addEventListener("change", function () { c = { type: type.value }; build(); });
    build();
    line.appendChild(type); line.appendChild(sub);
    line.read = function () { return cread(); };
    return line;
  }
  function extrasEditor(r, kind) {
    // Conditions, folded away until used so a simple rule stays short.
    var box = el("details", "r-extras");
    var count = (r.days && r.days.length < 7 ? 1 : 0) + (r.only ? 1 : 0) +
      (r.max_price !== undefined && r.max_price !== null ? 1 : 0) + (r.when ? r.when.length : 0);
    box.open = count > 0;
    box.appendChild(el("summary", "", "Lisäehdot" + (count ? " (" + count + ")" : ""),
      "Viikonpäivät, kellonajat, hintaraja ja muiden laitteiden tila. Kaikkien ehtojen täytyy täyttyä."));
    var days = daysPicker(r.days);
    box.appendChild(group("Viikonpäivät", days, kind === "between" || kind === "cheapest"
      ? "Päivät, joina aikaväli alkaa. Oletus: joka päivä."
      : "Sääntö toimii vain näinä päivinä. Oletus: joka päivä."));
    var onlySw = null, oStart = null, oEnd = null, price = null;
    if (kind === "event" || kind === "level") {
      onlySw = switchBtn(!!r.only, "Vain tiettynä aikana", kind === "level"
        ? "Laitteet ovat päällä vain tällä aikavälillä, muuten pois. Oletus: aina."
        : "Toiminnot tehdään vain tällä aikavälillä. Oletus: aina.");
      onlySw.lastChild.textContent = "Vain tiettynä aikana";
      oStart = el("input"); oStart.type = "time"; oStart.value = r.only ? r.only.start : "16:00";
      oEnd = el("input"); oEnd.type = "time"; oEnd.value = r.only ? r.only.end : "23:00";
      oStart.setAttribute("aria-label", "Alkaen"); oEnd.setAttribute("aria-label", "Asti");
      var oLine = el("div", "r-line"); oLine.appendChild(oStart); oLine.appendChild(oEnd);
      oLine.hidden = !r.only;
      onlySw.addEventListener("click", function () {
        var on = onlySw.getAttribute("aria-checked") !== "true";
        onlySw.setAttribute("aria-checked", on ? "true" : "false");
        oLine.hidden = !on;
      });
      box.appendChild(onlySw); box.appendChild(oLine);
    }
    if (kind !== "cheapest") {
      price = numInput(r.max_price, "Hinta enintään");
      price.placeholder = "ei rajaa";
      box.appendChild(field("Hinta enintään (snt/kWh)", price,
        "Vain kun sähkön pörssihinta on enintään tämä (ilman siirtoa ja veroja; ALV mukana, jos ALV-kytkin on päällä). " +
        (kind === "event" ? "" : "Kalliimmalla laitteet ovat pois. ") + "Tyhjä: ei rajaa."));
    }
    var whenBox = el("div", "r-list");
    var readWhen = listEditor(whenBox, r.when, condRow, "Lisää ehto", 5, 0);
    box.appendChild(group("Vain kun", whenBox, (kind === "event" ? "Toiminnot tehdään vain, kun nämä pitävät paikkansa."
      : "Laitteet ovat päällä vain, kun nämä pitävät paikkansa; muuten pois.") +
      " Esim. vain kun joku on kotona tai kun ulkona on alle 0 °C."));
    box.read = function (out) {
      var d = days.read();
      if (d.length < 7) out.days = d;
      if (onlySw && onlySw.getAttribute("aria-checked") === "true") out.only = { start: oStart.value, end: oEnd.value };
      if (price && price.value !== "") out.max_price = Number(price.value);
      var w = readWhen();
      if (w.length) out.when = w;
      return out;
    };
    return box;
  }
  function editRule(existing, asCopy) {
    var r = existing ? JSON.parse(JSON.stringify(existing.rule)) : { kind: "between" };
    if (asCopy) { existing = null; r.name = (r.name ? r.name + " " : "") + "(kopio)"; }
    $dTitle.textContent = existing ? "Muokkaa sääntöä" : "Uusi sääntö";
    $dBody.textContent = "";
    var form = el("div", "rule-form");
    var name = el("input"); name.type = "text"; name.maxLength = 60; name.value = r.name || "";
    name.placeholder = "Esim. Aamun pistokkeet";
    form.appendChild(field("Nimi", name, "Vapaaehtoinen."));
    var kind = select(["between", "event", "level", "cheapest"].map(function (k) { return [k, KIND_NAMES[k]]; }),
      r.kind, "Säännön tyyppi");
    var kindHelp = el("p", "detail r-help");
    form.appendChild(field("Tyyppi", kind, "Aikaväli, tapahtuma, anturin lukema tai sähkön halvimmat tunnit."));
    form.appendChild(kindHelp);
    var body = el("div");
    form.appendChild(body);
    var msg = el("p", "r-msg");
    var read = null;
    function build() {
      body.textContent = "";
      var k = kind.value;
      kindHelp.textContent = KIND_HELP[k];
      var extras = null;
      if (k === "between" || k === "cheapest" || k === "level") {
        var devs = devicePicker(r.keys, powerable);
        body.appendChild(group("Laitteet", devs, "Kaikki valitut laitteet kytketään yhdessä päälle ja pois."));
      }
      if (k === "between") {
        var start = momentEditor(r.start || { type: "time", at: "07:00" }, "Päälle");
        var end = momentEditor(r.end || { type: "time", at: "09:00" }, "Pois");
        body.appendChild(group("Päälle", start, "Auringon ajat tulevat Home Assistantista."));
        body.appendChild(group("Pois", end));
        extras = extrasEditor(r, k);
        read = function () { return extras.read({ kind: k, keys: devs.read(), start: start.read(), end: end.read() }); };
      } else if (k === "cheapest") {
        var hours = numInput(r.hours || 3, "Tunteja päällä", 0.25); hours.min = 0.25; hours.max = 24;
        var ws = el("input"); ws.type = "time"; ws.value = r.start || "22:00"; ws.setAttribute("aria-label", "Ikkunan alku");
        var we = el("input"); we.type = "time"; we.value = r.end || "07:00"; we.setAttribute("aria-label", "Ikkunan loppu");
        body.appendChild(field("Tunteja päällä", hours, "Montako tuntia laitteet ovat päällä ikkunan aikana. Oletus 3."));
        var win = el("div", "r-line"); win.appendChild(ws); win.appendChild(we);
        body.appendChild(group("Ikkuna (alku – loppu)", win, "Päivittäinen aikaväli, jonka halvimmat 15 minuutin jaksot valitaan."));
        extras = extrasEditor(r, k);
        read = function () {
          return extras.read({ kind: k, keys: devs.read(), hours: Number(hours.value), start: ws.value, end: we.value });
        };
      } else if (k === "level") {
        var sensor = deviceSelect(numeric, r.sensor, "Anturi");
        body.appendChild(field("Anturi", sensor, "Lämpötila, kosteus, hiilidioksidi tai muu lukema."));
        var mode = select([["below", "Päälle, kun alle"], ["above", "Päälle, kun yli"]],
          r.mode || "below", "Suunta");
        var onAt = numInput(r.on_at !== undefined ? r.on_at : 18, "Päälle-raja");
        var offAt = numInput(r.off_at !== undefined ? r.off_at : 19, "Pois-raja");
        var offLab = el("span", "r-unit");
        function modeText() { offLab.textContent = mode.value === "below" ? "pois, kun yli" : "pois, kun alle"; }
        mode.addEventListener("change", function () {
          modeText();
          var a = Number(onAt.value), b = Number(offAt.value);
          if (mode.value === "below" && b < a || mode.value === "above" && b > a) { onAt.value = b; offAt.value = a; }
        });
        modeText();
        var l1 = el("div", "r-line"); l1.appendChild(mode); l1.appendChild(onAt);
        var l2 = el("div", "r-line"); l2.appendChild(offLab); l2.appendChild(offAt);
        var lv = el("div"); lv.appendChild(l1); lv.appendChild(l2);
        body.appendChild(group("Rajat", lv,
          "Rajojen väli estää laitetta naksumasta: esim. päälle alle 18, pois yli 19. Välissä laite pysyy ennallaan."));
        extras = extrasEditor(r, k);
        read = function () {
          return extras.read({ kind: k, keys: devs.read(), sensor: sensor.value, mode: mode.value,
            on_at: Number(onAt.value), off_at: Number(offAt.value) });
        };
      } else {
        var tBox = el("div", "r-list");
        var readTriggers = listEditor(tBox, r.triggers, triggerRow, "Tai kun…", 5);
        body.appendChild(group("Kun", tBox, "Mikä tahansa näistä käynnistää säännön."));
        var aBox = el("div", "r-list");
        var readActions = listEditor(aBox, r.actions, actionRow, "Lisää toiminto", 10);
        body.appendChild(group("Tee", aBox, "Toiminnot tehdään järjestyksessä."));
        var offAfter = numInput(r.off_after, "Pois päältä minuutin kuluttua", 1);
        offAfter.min = 1; offAfter.max = 1440; offAfter.placeholder = "ei";
        body.appendChild(field("Pois päältä taas (min)", offAfter,
          "Päälle kytketyt laitteet sammutetaan näin monen minuutin kuluttua. Uusi käynnistys siirtää sammutusta. Tyhjä: ei sammuteta."));
        extras = extrasEditor(r, k);
        read = function () {
          var out = { kind: "event", triggers: readTriggers(), actions: readActions() };
          if (offAfter.value !== "") out.off_after = Number(offAfter.value);
          return extras.read(out);
        };
      }
      body.appendChild(extras);
    }
    kind.addEventListener("change", function () {
      // Keep the chosen devices when switching between the on/off kinds.
      var keep = r.keys || (r.actions || []).map(function (a) { return a.key; }).filter(Boolean);
      r = { kind: kind.value, keys: keep, actions: keep.map(function (k) {
        return { key: k, action: "power", value: true }; }) };
      build();
    });
    build();
    var buttons = el("div", "r-buttons");
    var save = el("button", "primary", "Tallenna");
    save.type = "button";
    save.addEventListener("click", function () {
      var rule = read();
      rule.name = name.value;
      var payload = { rule: rule };
      if (existing) payload.id = existing.id;
      save.disabled = true;
      post("rules", payload).then(function (res) {
        save.disabled = false;
        if (!res[0]) { msg.textContent = res[1].detail || "Tallennus ei onnistunut."; return; }
        $dialog.close();
        load();
      }).catch(function () { save.disabled = false; msg.textContent = "Yhteys palveluun katkesi."; });
    });
    buttons.appendChild(save);
    var cancel = el("button", "", "Peruuta");
    cancel.type = "button";
    cancel.addEventListener("click", function () { $dialog.close(); });
    buttons.appendChild(cancel);
    if (existing) {
      var copy = el("button", "", "Kopioi", "Uusi sääntö tämän pohjalta.");
      copy.type = "button";
      copy.addEventListener("click", function () {
        var draft = read();
        draft.name = name.value;
        editRule({ rule: draft }, true);
      });
      buttons.appendChild(copy);
      var del = el("button", "danger", "Poista sääntö");
      del.type = "button";
      del.addEventListener("click", function () {
        if (del.dataset.sure !== "1") { del.dataset.sure = "1"; del.textContent = "Vahvista poisto"; return; }
        post("rules", { id: existing.id, delete: true }).then(function () { $dialog.close(); load(); });
      });
      buttons.appendChild(del);
    }
    form.appendChild(msg);
    form.appendChild(buttons);
    $dBody.appendChild(form);
    if (typeof $dialog.showModal === "function") { if (!$dialog.open) $dialog.showModal(); }
    else $dialog.setAttribute("open", "");
  }
  document.getElementById("dev-rule-add").addEventListener("click", function () { editRule(null); });

  function render() {
    var data = DATA;
    if (!data || !data.configured) { $section.hidden = true; return; }
    $section.hidden = false;
    var conn = CONNECTION[data.connection];
    $conn.hidden = !conn;
    if (conn) { $conn.className = "badge " + conn[0]; $conn.textContent = conn[1]; }
    var shown = data.areas.filter(function (a) { return a.devices.length; });
    $note.textContent = !data.registry
      ? "Home Assistantin huone- ja laitetietoja ei ole vielä saatu" +
        (data.registry_error ? " (" + data.registry_error + ")." : ".")
      : !shown.length ? "Laitteet näkyvät täällä, kun lisäät ne Home Assistantissa johonkin alueeseen (huoneeseen)." : "";
    $areas.textContent = "";
    data.areas.forEach(function (a, i) {
      var b = areaBlock(a, i);
      if (b) $areas.appendChild(b);
    });
    renderAutos(data.automations || []);
    renderRules(data.rules || []);
  }
  function load() {
    if (LOADING || $dialog.open) return;   // an open editor or detail is not redrawn under the user
    LOADING = true;
    var site = vkSite();
    fetch("api/devices?site=" + site).then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        LOADING = false;
        if (!data || site !== vkSite()) return;
        DATA = data;
        if (BUSY) return;   // a slider is held: the next refresh draws it
        render();
      }).catch(function () { LOADING = false; });
  }
  // --- detail dialog: history, name, visibility, colour temperature ---
  function findDevice(key) {
    var found = null;
    (DATA ? DATA.areas : []).forEach(function (a) {
      a.devices.forEach(function (d) { if (d.key === key) found = d; });
    });
    return found;
  }
  function chart(box, points, onoff, unit) {
    box.textContent = "";
    if (points.length < 2) { box.appendChild(el("p", "detail", "Historiaa ei ole vielä kertynyt.")); return; }
    var NS = "http://www.w3.org/2000/svg", W = 520, H = 170, L = 38, B = 18;
    var t0 = new Date(points[0][0]).getTime(), t1 = new Date(points[points.length - 1][0]).getTime();
    var vals = points.map(function (p) { return p[1]; });
    var lo = onoff ? 0 : Math.min.apply(null, vals), hi = onoff ? 1 : Math.max.apply(null, vals);
    if (hi - lo < 1e-9) { lo -= 1; hi += 1; }
    function X(t) { return L + (t - t0) / Math.max(1, t1 - t0) * (W - L - 6); }
    function Y(v) { return 6 + (1 - (v - lo) / (hi - lo)) * (H - B - 12); }
    var s = document.createElementNS(NS, "svg");
    s.setAttribute("viewBox", "0 0 " + W + " " + H);
    s.setAttribute("role", "img");
    [lo, (lo + hi) / 2, hi].forEach(function (v) {
      var g = document.createElementNS(NS, "line");
      g.setAttribute("class", "grid");
      g.setAttribute("x1", L); g.setAttribute("x2", W - 6);
      g.setAttribute("y1", Y(v)); g.setAttribute("y2", Y(v));
      s.appendChild(g);
      if (onoff && v === (lo + hi) / 2) return;
      var tx = document.createElementNS(NS, "text");
      tx.setAttribute("x", 2); tx.setAttribute("y", Y(v) + 3);
      tx.textContent = onoff ? (v ? "päällä" : "pois") : num2.format(v);
      s.appendChild(tx);
    });
    [t0, t1].forEach(function (t, i) {
      var tx = document.createElementNS(NS, "text");
      tx.setAttribute("x", i ? W - 6 : L); tx.setAttribute("y", H - 3);
      tx.setAttribute("text-anchor", i ? "end" : "start");
      tx.textContent = dayClock.format(new Date(t));
      s.appendChild(tx);
    });
    if (onoff) {
      var d = "M" + X(t0) + " " + Y(0);
      points.forEach(function (p, i) {
        var x = X(new Date(p[0]).getTime());
        if (i) d += " H" + x.toFixed(1);
        d += " V" + Y(p[1] >= 0.5 ? 1 : p[1]).toFixed(1);
      });
      d += " H" + X(t1).toFixed(1) + " V" + Y(0) + " Z";
      var path = document.createElementNS(NS, "path");
      path.setAttribute("class", "onoff");
      path.setAttribute("d", d);
      s.appendChild(path);
    } else {
      var pl = document.createElementNS(NS, "polyline");
      pl.setAttribute("points", points.map(function (p) {
        return X(new Date(p[0]).getTime()).toFixed(1) + "," + Y(p[1]).toFixed(1);
      }).join(" "));
      s.appendChild(pl);
    }
    s.setAttribute("aria-label", "Historia" + (unit ? " (" + unit + ")" : ""));
    box.appendChild(s);
  }
  function openDialog(key) {
    var d = findDevice(key);
    if (!d) return;
    OPEN_KEY = key;
    if (d.new) post("layout", { key: key, seen: true }).then(load);
    $dTitle.textContent = d.name;
    $dBody.textContent = "";
    var history = ["temperature", "humidity", "co2", "air", "power", "energy", "illuminance", "battery",
                   "number", "light", "plug", "switch", "fan", "binary"].indexOf(d.kind) >= 0;
    if (history) {
      var seg = el("span", "seg");
      seg.setAttribute("role", "group");
      seg.setAttribute("aria-label", "Ajanjakso");
      var box = el("div", "d-chart");
      var onoff = ["light", "plug", "switch", "fan", "binary"].indexOf(d.kind) >= 0;
      function show(hours) {
        seg.querySelectorAll("button").forEach(function (b) {
          b.setAttribute("aria-pressed", Number(b.dataset.h) === hours ? "true" : "false");
        });
        fetch("api/devices/history?site=" + vkSite() + "&key=" + encodeURIComponent(key) + "&hours=" + hours)
          .then(function (r) { return r.ok ? r.json() : { points: [] }; })
          .then(function (j) { chart(box, j.points || [], onoff, d.unit || UNITS[d.kind] || ""); });
      }
      [[24, "Päivä"], [168, "Viikko"], [720, "Kuukausi"], [8760, "Vuosi"]].forEach(function (o) {
        var b = el("button", "", o[1]);
        b.type = "button";
        b.dataset.h = o[0];
        b.addEventListener("click", function () { show(o[0]); });
        seg.appendChild(b);
      });
      $dBody.appendChild(seg);
      $dBody.appendChild(box);
      show(24);
    }
    if (d.kind === "light" && d.caps && d.caps.ct && d.available) {
      var lab = el("label", "fld", "Värilämpötila (K)", "Lämmin 2700 K, viileä 5000 K tai yli.");
      var msg = el("div", "t-msg");
      lab.appendChild(slider(d.caps.ct_min, d.caps.ct_max, 50, d.color_temp || 3000, "Värilämpötila",
        function (val) { act(d, "color_temp", val, msg); }));
      $dBody.appendChild(lab);
      $dBody.appendChild(msg);
    }
    if (d.kind === "temperature" && DATA.rooms && DATA.rooms.length && DATA.room_target === "cottage") {
      var mine = DATA.rooms.filter(function (r) { return r.sensor === key; })[0];
      var roomSel = select([["", "Ei mikään"]].concat(DATA.rooms.map(function (r) {
        return [r.id, r.name + (r.sensor && r.sensor !== key ? " (korvaa nykyisen anturin)" : "")];
      })), mine ? mine.id : "", "Huone, jonka lämpötilana tätä käytetään");
      var roomMsg = el("div", "t-msg");
      roomSel.addEventListener("change", function () {
        if (!roomSel.value) { roomMsg.textContent = "Oletusanturin saa takaisin huoneen kortilta."; return; }
        post("room_sensor", { room: roomSel.value, key: key }).then(function (res) {
          roomMsg.className = res[0] ? "t-msg" : "t-msg err";
          roomMsg.textContent = res[0] ? "Lähetetty mökille. Anturi siirtyy huoneen kortille."
            : (res[1].detail || "Tallennus ei onnistunut.");
        });
      });
      $dBody.appendChild(field("Mökin huoneen lämpötilana", roomSel,
        "Mökin ohjain lämmittää huonetta tämän anturin mukaan. Oletusanturin saa takaisin huoneen kortilta. " +
        "Oletus: ei mikään."));
      $dBody.appendChild(roomMsg);
    } else if (d.kind === "temperature" && DATA.rooms && DATA.rooms.length) {
      // One sensor can serve several pumps (one thermometer between two rooms).
      var pumps = el("fieldset", "d-pumps");
      pumps.appendChild(el("legend", "", "Pumppujen huonelämpötilana",
        "Valitut pumput ohjataan ja niiden huone opitaan tämän anturin mukaan pumpun oman lukeman sijaan. " +
        "Sama anturi voi ohjata useaa pumppua. Jos anturi ei vastaa, käytetään pumpun omaa lukemaa. Oletus: pois."));
      var pumpMsg = el("div", "t-msg");
      DATA.rooms.forEach(function (r) {
        var other = r.sensor && r.sensor !== key;
        var line = el("div", "d-row");
        line.appendChild(el("span", "", r.name, other
          ? "Pumppu käyttää nyt anturia " + devName(r.sensor) + ". Kytkin vaihtaa sen tähän anturiin."
          : "Seuraako " + r.name + " -pumppu tätä anturia."));
        var sw = switchBtn(r.sensor === key, r.name + ": seuraa tätä anturia");
        sw.lastChild.textContent = r.sensor === key ? "Seuraa" : other ? "Toinen anturi" : "Oma mittari";
        sw.addEventListener("click", function () {
          var want = sw.getAttribute("aria-checked") !== "true";
          sw.disabled = true;
          post("room_sensor", { room: r.id, key: want ? key : null }).then(function (res) {
            sw.disabled = false;
            pumpMsg.className = res[0] ? "t-msg" : "t-msg err";
            pumpMsg.textContent = res[0] ? "" : (res[1].detail || "Tallennus ei onnistunut.");
            if (!res[0]) return;
            r.sensor = want ? key : null;
            sw.setAttribute("aria-checked", want ? "true" : "false");
            sw.lastChild.textContent = want ? "Seuraa" : "Oma mittari";
          });
        });
        line.appendChild(sw);
        pumps.appendChild(line);
      });
      $dBody.appendChild(pumps);
      $dBody.appendChild(pumpMsg);
    }
    var nameLab = el("label", "fld", "Nimi Villa Kuppiksessa",
      "Tyhjä nimi palauttaa Home Assistantin nimen (" + d.ha_name + ").");
    var input = el("input");
    input.type = "text";
    input.maxLength = 60;
    input.value = d.name === d.ha_name ? "" : d.name;
    input.placeholder = d.ha_name;
    input.addEventListener("change", function () {
      post("layout", { key: key, name: input.value }).then(function () {
        $dTitle.textContent = input.value.trim() || d.ha_name;
        load();
      });
    });
    nameLab.appendChild(input);
    $dBody.appendChild(nameLab);
    var row = el("div", "d-row");
    row.appendChild(el("span", "", "Näkyy sivulla", "Piilotetun laitteen saa takaisin Järjestä-tilassa. Oletus: näkyy."));
    var vis = switchBtn(!d.hidden, "Näkyy sivulla");
    vis.lastChild.textContent = d.hidden ? "Pois" : "Näkyy";
    vis.addEventListener("click", function () {
      var want = vis.getAttribute("aria-checked") !== "true";
      vis.setAttribute("aria-checked", want ? "true" : "false");
      vis.lastChild.textContent = want ? "Näkyy" : "Pois";
      d.hidden = !want;
      post("layout", { key: key, hidden: !want }).then(load);
    });
    row.appendChild(vis);
    $dBody.appendChild(row);
    var dl = el("dl");
    function info(k, v) { if (!v) return; dl.appendChild(el("dt", "", k)); dl.appendChild(el("dd", "", v)); }
    var area = (DATA.areas.filter(function (a) { return a.id === d.area; })[0] || {}).name;
    info("Huone", area);
    info("Laite", d.device && d.device.name);
    info("Valmistaja", d.device && [d.device.manufacturer, d.device.model].filter(Boolean).join(" "));
    info("Tunniste", d.entity_id);
    info("Muuttui", d.changed ? dayClock.format(new Date(d.changed)) : "");
    $dBody.appendChild(dl);
    if (typeof $dialog.showModal === "function") $dialog.showModal(); else $dialog.setAttribute("open", "");
  }
  // --- a pump's room sensor, opened from the pump card's gauge ---
  function homeSensors() {
    if (!DATA || !DATA.configured || DATA.site !== "home" || DATA.room_target !== "pump") return null;
    var out = allDevices().filter(function (x) { return x.d.kind === "temperature"; });
    return out.length ? out : null;
  }
  window.vkHomeSensors = homeSensors;
  window.vkRoomSensorPicker = function (unitId) {
    var sensors = homeSensors();
    var room = sensors && DATA.rooms.filter(function (r) { return r.id === unitId; })[0];
    if (!room) return;
    OPEN_KEY = null;
    $dTitle.textContent = room.name;
    $dBody.textContent = "";
    var opts = [["", "Pumpun oma anturi"]].concat(sensors.map(function (x) {
      return [x.d.key, x.d.name + " (" + x.area + ")"];
    }));
    if (room.sensor && !sensors.some(function (x) { return x.d.key === room.sensor; })) {
      opts.push([room.sensor, "(puuttuva anturi)"]);
    }
    var sel = select(opts, room.sensor || "", "Huoneen lämpötila");
    var msg = el("div", "t-msg");
    var shared = el("p", "d-note");
    function sharing() {
      var others = DATA.rooms.filter(function (r) { return r.id !== room.id && r.sensor && r.sensor === sel.value; })
        .map(function (r) { return r.name; });
      shared.textContent = others.length ? "Samaa anturia seuraa myös " + others.join(" ja ") + "." : "";
    }
    sel.addEventListener("change", function () {
      var key = sel.value || null;
      sel.disabled = true;
      post("room_sensor", { room: room.id, key: key }).then(function (res) {
        sel.disabled = false;
        msg.className = res[0] ? "t-msg" : "t-msg err";
        msg.textContent = res[0] ? (key ? "Tallennettu. Pumppu seuraa nyt tätä anturia." : "Tallennettu. Pumppu käyttää omaa anturiaan.")
          : (res[1].detail || "Tallennus ei onnistunut.");
        if (res[0]) {
          room.sensor = key;
          sharing();
          if (typeof refresh === "function") refresh();
        } else {
          sel.value = room.sensor || "";
        }
      });
    });
    $dBody.appendChild(field("Huoneen lämpötila", sel,
      "Automaatio käynnistää, sammuttaa ja säätää pumppua niin, että valittu anturi pysyy tavoitteessa: " +
      "pumpun asetuslämpöön lisätään pumpun oman lukeman ja anturin ero. Sama anturi voi ohjata useaa pumppua. " +
      "Jos anturi ei ilmoita lukemaa puoleentoista tuntiin, käytetään pumpun omaa lukemaa. Oletus: pumpun oma anturi."));
    $dBody.appendChild(msg);
    $dBody.appendChild(shared);
    sharing();
    if (typeof $dialog.showModal === "function") $dialog.showModal(); else $dialog.setAttribute("open", "");
  };
  $dialog.addEventListener("close", function () { OPEN_KEY = null; load(); });
  $edit.addEventListener("click", function () {
    EDIT = !EDIT;
    $edit.setAttribute("aria-checked", EDIT ? "true" : "false");
    render();
  });
  document.addEventListener("vk-page", function () { DATA = null; render(); load(); });
  window.loadDevices = load;
  load();
  vkEvery(load, 10000);
})();
"""
