# Open questions

Answers go into the relevant design doc; then remove the question here. Never send pairing codes, serial numbers or passwords.

Two-site Home Assistant (20 October 2026), none blocking the documentation or the mocked gateway:

1. ~~Hardware~~ Settled 20 October 2026: HP MP9 G4 mini-PC at the cottage, HP OmniBook laptop at home, both Debian 13 hosts with a Home Assistant OS virtual machine. See [deployment](architecture.md#deployment).
2. **Safe fallback setpoint** per cottage room, once the heaters' real minimum over Matter is known (H2.1).
3. **The partly work-managed phone:** does its VPN policy allow Tailscale? Test before H3.1.

## Settled

- `Automaatio` off at the cottage (owner, 25 October 2026): heating optimization stops and the heaters fall back to the active fallback setpoint (for example 5 °C).
- Name Villa Kuppis; public repository [Vkuparin/villa-kuppis](https://github.com/Vkuparin/villa-kuppis) under Apache License 2.0. Finnish UI; technical docs in English.
- The goal is one app instead of many; take the simplest working route to each device, cloud included.
- Two-site Home Assistant (20 October 2026): Villa Kuppis (Compose) and a Home Assistant OS virtual machine on an always-on Debian home server, another Home Assistant OS VM on a Debian cottage mini-PC (revised the same evening from Home Assistant Container and bare-metal HAOS). Browser/PWA, no Android app. Tailscale between browsers, the home server and the cottage Home Assistant (free plan). Until the cutover, the service runs on the home Windows 11 PC.
- Household members use the app with full control, signed in to Tailscale with their own Google accounts. No separate app login. Site health and stale data shown in the web app.
- One Home Assistant per site on its own mini-PC (replaces "no Home Assistant or cottage computer"). No Premium.
- Home: three indoor units (LN25 on outdoor unit 1; LN35 and LN25 on outdoor unit 2), all in MELCloud Home and controlled through its cloud. Summer cooling matters most; radiators are geothermal and outside our control.
- Cottage: four Mill heaters and two ALPSTUGA sensors in Google Home over Matter, arriving early November 2026. Large room: 2 × 1050 W and 1 × 500 W heaters plus one ALPSTUGA; small room: 1 × 1050 W heater plus one ALPSTUGA. Heaters follow the ALPSTUGA readings through the cottage Home Assistant's local controller, which gets each room's target from Villa Kuppis. Network stays NE200-Outdoor and Archer BE230. Mill built-in frost protection; no water pipes.
- Spot prices: the raw spot price only. Fixed thresholds first, then a smart maintenance heating optimizer (minimum and maximum storage temperature, low and high price, learned rates, FMI forecast); settings per site or per room. Pump cooling in reverse; no season mode.
- Summer cooling defaults: maximum 25 °C, precooling 21 °C, switching off allowed with a minimum off time. Separate home-away button. Quiet fan at night in chosen rooms. All settings changeable in the app.
- Visit: arrival time and target; cheapest heat-up that reaches the target in time; ended by button.
- Time-series graphs of all sensor readings, setpoints and prices.
- Development tools (Python, Android SDK and so on) are installed by the agent on the owner's PC when implementation starts.
- Europe/Helsinki for both sites.
