"""Finnish texts for the few messages the services still produce in English.

The pump and Home Assistant layers (and their tests) keep their English
messages. What the web app sends to the browser passes through
``translate_payload``, so the UI shows Finnish, also for event rows stored
before the translation existed. A text that matches no pattern stays as it is.
"""

from __future__ import annotations

import re

_PHASES = {
    "state read": "Tilan luku",
    "cloud startup": "Pilvipalvelun käynnistys",
    "cloud read": "Pilvipalvelun luku",
    "command": "Komento",
}

_CAUSES = (
    ("invalid JSON response", "virheellinen JSON-vastaus"),
    (r"HTTP (\d+)", r"HTTP \1"),
    ("A MELCloud Home error occurred.", "MELCloud Home -virhe."),
    ("Authentication failed. Check the MELCloud Home credentials.",
     "Kirjautuminen epäonnistui. Tarkista MELCloud Home -tunnukset."),
    ("A MELCloud Home request timed out.", "MELCloud Home -pyyntö aikakatkaistiin."),
    ("The requested unit was not found in MELCloud Home.", "Laitetta ei löytynyt MELCloud Homesta."),
    ("The MELCloud Home stream is unavailable.", "MELCloud Home -tietovirta ei ole käytettävissä."),
    ("A MELCloud Home request failed. Check the connection and try again.",
     "MELCloud Home -pyyntö epäonnistui. Tarkista yhteys ja yritä uudelleen."),
    (r"A MELCloud Home operation exceeded its (\d+) second deadline.",
     r"MELCloud Home -toiminto ylitti \1 sekunnin aikarajan."),
)

_EXACT = (
    # pump_service: validation and request answers
    ("value must be true or false", "Arvon on oltava päällä tai pois."),
    ("value must be a number", "Arvon täytyy olla luku."),
    ("the unit has not reported an operation mode", "Laite ei ole ilmoittanut toimintatilaa."),
    ("a setpoint is not available in fan mode", "Tavoitelämpötilaa ei voi säätää puhallustilassa."),
    ("the unit has not reported temperature bounds for this mode",
     "Laite ei ole ilmoittanut lämpötilarajoja tälle tilalle."),
    (r"value must be between ([\d.,-]+) and ([\d.,-]+)", r"Arvon on oltava \1–\2."),
    ("the value is not on the unit's half-degree steps", "Arvon on oltava laitteen puolen asteen portaissa."),
    ("the value is not on the unit's one-degree steps", "Arvon on oltava laitteen asteen portaissa."),
    ("the mode is not supported by this unit", "Laite ei tue tätä toimintatilaa."),
    ("value must be a fan speed name", "Arvon on oltava puhallusnopeus."),
    ("this unit has no automatic fan speed", "Laitteella ei ole automaattista puhallusnopeutta."),
    ("this unit has no fan speed that high", "Laitteella ei ole näin suurta puhallusnopeutta."),
    ("the fan speed is not supported by this unit", "Laite ei tue tätä puhallusnopeutta."),
    (r"action must be one of (.+)", "Tuntematon toimenpide."),
    ("the pump service is not running", "Pumppupalvelu ei ole käynnissä."),
    ("the pump service is shutting down", "Pumppupalvelu sammuu."),
    ("the unit is unknown to the service", "Palvelu ei tunne laitetta."),
    ("the unit reports no state", "Laite ei ilmoita tilaansa."),
    ("a previous change for this unit is still being confirmed",
     "Aiempi muutos on vielä vahvistumassa; yritä hetken kuluttua."),
    ("another change for this unit was still being confirmed",
     "Toinen muutos oli vielä vahvistumassa."),
    ("the command could not be validated", "Komentoa ei voitu tarkistaa."),
    ("the service is shutting down", "Palvelu sammuu."),
    ("the service restarted before the change was confirmed",
     "Palvelu käynnistyi uudelleen ennen kuin muutos ehdittiin vahvistaa."),
    ("the unit was not found in MELCloud Home", "Laitetta ei löytynyt MELCloud Homesta."),
    ("the write response failed but the change was confirmed",
     "Kirjoituksen vastaus epäonnistui, mutta muutos vahvistui."),
    ("site must be home or cottage", "Kohteen on oltava koti tai mökki."),
    # ha_pumps
    ("one change at a time", "Vain yksi muutos kerrallaan."),
    (r"mode (\S+) is not offered by Home Assistant", r"Home Assistant ei tarjoa toimintatilaa \1."),
    (r"fan speed (\S+) is not offered by Home Assistant", r"Home Assistant ei tarjoa puhallusnopeutta \1."),
    (r"unsupported change (\S+)", "Muutosta ei tueta."),
    ("the unit is off; Home Assistant would switch it on to change the mode",
     "Laite on pois päältä, ja Home Assistant kytkisi sen päälle toimintatilan vaihdossa."),
    # ha_gateway
    ("the device is read-only", "Laitetta ei voi ohjata."),
    ("the device is unavailable in Home Assistant", "Laite ei ole käytettävissä Home Assistantissa."),
    ("the device cannot be turned on and off", "Laitetta ei voi kytkeä päälle tai pois."),
    ("the mode is not supported by this device", "Laite ei tue tätä toimintatilaa."),
    ("the fan speed is not supported by this device", "Laite ei tue tätä puhallusnopeutta."),
    ("the device has no setpoint", "Laitteella ei ole tavoitelämpötilaa."),
    ("the device has not reported its range", "Laite ei ole ilmoittanut sallittua aluetta."),
    (r"value must be in steps of ([\d.,]+)", r"Arvon on oltava \1 asteen portaissa."),
    ("the device is not bound at this site", "Laitetta ei ole liitetty tähän kohteeseen."),
    (r"(\S+) is missing in Home Assistant; check the binding",
     r"\1 puuttuu Home Assistantista; tarkista liitos."),
    ("another command for this device is pending", "Toinen komento on vielä kesken."),
    ("Home Assistant is not connected", "Home Assistantiin ei ole yhteyttä."),
    ("the device has not reported its state", "Laite ei ole ilmoittanut tilaansa."),
    ("already at the requested value", "Jo pyydetyssä arvossa."),
    (r"Home Assistant refused the command: (.*)", r"Home Assistant hylkäsi komennon: \1"),
    ("no answer from Home Assistant; the change may have been applied",
     "Home Assistant ei vastannut; muutos on voinut mennä läpi."),
    (r"Home Assistant accepted the command but did not report the new value within ([\d.,]+) s",
     r"Home Assistant hyväksyi komennon mutta ei ilmoittanut uutta arvoa \1 sekunnissa."),
    ("confirmed by a later report", "Vahvistui myöhemmästä tilatiedosta."),
    ("the connection to Home Assistant closed", "Yhteys Home Assistantiin katkesi."),
    (r"access token refused: .*", "Home Assistant hylkäsi käyttöoikeustunnuksen."),
    (r"[A-Z][A-Za-z]*(?:Error|Exception|Closed|Timeout)\w*(?:: .*)?", "Yhteysvirhe Home Assistantiin."),
    # web app request checks
    ("unknown cottage room", "Tuntematon mökin huone."),
    ("unknown API path", "Tuntematon polku."),
    ("use POST for this path", "Tämä polku vaatii POST-pyynnön."),
    ("use GET for this path", "Tämä polku vaatii GET-pyynnön."),
    ("same origin required", "Pyyntö ei tullut samasta osoitteesta."),
    ("local host required", "Palveluun pääsee vain paikallisesta osoitteesta."),
    ("unknown client address", "Tuntematon asiakasosoite."),
    ("exactly one Host header is required", "Pyynnössä on oltava täsmälleen yksi Host-otsake."),
    ("duplicate Origin header", "Pyynnössä on useita Origin-otsakkeita."),
    ("the host does not match the .*", "Osoite ei vastaa palvelun osoitetta."),
    ("the origin does not match the .*", "Lähde ei vastaa palvelun osoitetta."),
    ("the Tailscale user login header .*", "Tailscale-käyttäjän tunnistus puuttuu tai on virheellinen."),
    ("duplicate Tailscale user .*", "Tailscale-käyttäjän tunnistus on päällekkäinen."),
    ("no Home Assistant user", "Home Assistant -käyttäjää ei tunnistettu."),
    ("JSON body required", "Pyynnön sisällön on oltava JSON."),
    ("the request body is missing or too large", "Pyynnön sisältö puuttuu tai on liian suuri."),
    ("the request body is not valid JSON", "Pyynnön sisältö ei ole kelvollista JSONia."),
    ("the request body must be a JSON object", "Pyynnön sisällön on oltava JSON-olio."),
    ("scope and key must be strings", "Kohde ja avain on annettava tekstinä."),
    ("value or reset is required", "Arvo tai nollaus puuttuu."),
    ("unit and action must be strings", "Laite ja toimenpide on annettava tekstinä."),
    ("unit must be a string", "Laite on annettava tekstinä."),
    ("unit is required", "Laite puuttuu."),
    ("away must be true or false", "Poissaolon on oltava päällä tai pois."),
    (r"on must be true or false, site home or cottage", "Arvon on oltava päällä tai pois ja kohteen koti tai mökki."),
    ("end must be an ISO time with a zone", "Lopun on oltava aika aikavyöhykkeen kanssa."),
)

_EXACT_RE = [(re.compile(p + r"\Z"), r) for p, r in _EXACT]
_CAUSE_RE = [(re.compile(p + r"\Z"), r) for p, r in _CAUSES]
_FAILED_RE = re.compile(r"(" + "|".join(re.escape(p) for p in _PHASES) + r") failed: (.+)\Z", re.S)

# Who made a change, when the actor is a bare technical name.
_ACTORS = {"web": "Verkkosovellus"}


def fi_text(text: str) -> str:
    """The Finnish form of a known English message, else the text itself."""
    if not text or not text.isascii():
        return text
    match = _FAILED_RE.fullmatch(text)
    if match:
        cause = match.group(2)
        for regex, repl in _CAUSE_RE:
            if regex.fullmatch(cause):
                cause = regex.sub(repl, cause)
                break
        return f"{_PHASES[match.group(1)]} epäonnistui: {cause}"
    for regex, repl in _EXACT_RE:
        if regex.fullmatch(text):
            return regex.sub(repl, text)
    return text


_KEYS = {"detail", "error", "reason", "last_error"}


def translate_payload(obj):
    """Translate English messages in the ``detail``, ``error`` and ``reason``
    fields (and bare actors) of an API payload, in place of a copy."""
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            if isinstance(value, str) and key in _KEYS:
                out[key] = fi_text(value)
            elif isinstance(value, str) and key == "actor":
                out[key] = _ACTORS.get(value, value)
            else:
                out[key] = translate_payload(value)
        return out
    if isinstance(obj, list):
        return [translate_payload(v) for v in obj]
    return obj
