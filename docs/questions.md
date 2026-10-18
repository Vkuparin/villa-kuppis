# Open questions

Answers go into the relevant design doc; then remove the question here. Never send pairing codes, serial numbers or passwords.

1. **Pumps:** does the LN35 (MAC-577IF2-E) appear in your MELCloud Home app next to the upstairs LN25, or only in the old MELCloud app? And when you can, which adapter does the downstairs LN25 have?
2. Who else uses the app: household members, guests? Should anyone have read-only access?
3. Do you want notifications, for example a heater going offline or a command failing? Through which channel?
4. For summer cooling: roughly which rules do you have in mind (comfort temperature, precooling when cheap, off when expensive)? This can wait until spring.

## Settled

- Name Villa Kuppis; public repository [Vkuparin/villa-kuppis](https://github.com/Vkuparin/villa-kuppis) under Apache License 2.0. Finnish UI; technical docs in English.
- The goal is one app instead of many; take the simplest working route to each device, cloud included.
- Python service on the home Windows 11 PC, almost always on. One web app for desktop and Android phones, over Tailscale (free plan).
- No Home Assistant box, VM or cottage computer unless every other route fails. No Premium.
- Home: three indoor units (LN25 on outdoor unit 1; LN35 and LN25 on outdoor unit 2). Adapters: LN35 MAC-577IF2-E, LN25 upstairs MAC-597IFB-E, LN25 downstairs to check. MELCloud Home cloud for every unit it covers. Summer cooling matters most; radiators are geothermal and outside our control.
- Cottage: four Mill heaters in Mill cloud mode, arriving early November 2026; two ALPSTUGA sensors in Google Home. Network stays NE200-Outdoor and Archer BE230. Mill built-in frost protection; no water pipes.
- Spot-price automation is a high priority. Thresholds compare against the raw spot price only. Cottage base 5 °C; visit mode starts and ends only by button.
- Europe/Helsinki for both sites.
