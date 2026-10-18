# Open questions

Answers go into the relevant design doc; then remove the question here. Never send pairing codes, serial numbers or passwords.

None at the moment.

## Settled

- Name Villa Kuppis; public repository [Vkuparin/villa-kuppis](https://github.com/Vkuparin/villa-kuppis) under Apache License 2.0. Finnish UI; technical docs in English.
- The goal is one app instead of many; take the simplest working route to each device, cloud included.
- Python service on the home Windows 11 PC, almost always on. Desktop web app; native Kotlin Android app with the web app inside and the cottage functions. Tailscale between phones and PC (free plan).
- Household members use the app with full control, each with their own Google account (Google Home membership for the cottage, Tailscale with Google sign-in for the PC). No separate app login. Android notifications for failed uploads, offline devices and visit plans that cannot reach the target.
- No Home Assistant box, VM or cottage computer unless every other route fails. No Premium.
- Home: three indoor units (LN25 on outdoor unit 1; LN35 and LN25 on outdoor unit 2), all in MELCloud Home and controlled through its cloud. Summer cooling matters most; radiators are geothermal and outside our control.
- Cottage: four Mill heaters and two ALPSTUGA sensors in Google Home over Matter, arriving early November 2026. Large room: 2 × 1050 W and 1 × 500 W heaters plus one ALPSTUGA; small room: 1 × 1050 W heater plus one ALPSTUGA. Heaters follow the ALPSTUGA readings through sensor-driven automations uploaded daily by the Android app. Network stays NE200-Outdoor and Archer BE230. Mill built-in frost protection; no water pipes.
- Spot prices: the raw spot price only. Fixed thresholds first, then a smart maintenance heating optimizer (minimum and maximum storage temperature, low and high price, learned rates, FMI forecast); settings per site or per room. Pump cooling in reverse; no season mode.
- Summer cooling defaults: maximum 25 °C, precooling 21 °C, switching off allowed with a minimum off time. Separate home-away button. Quiet fan at night in chosen rooms. All settings changeable in the app.
- Visit: arrival time and target; cheapest heat-up that reaches the target in time; ended by button.
- Time-series graphs of all sensor readings, setpoints and prices.
- Development tools (Python, Android SDK and so on) are installed by the agent on the owner's PC when implementation starts.
- Europe/Helsinki for both sites.
