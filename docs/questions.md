# Open questions

Answers go into the relevant design doc; then remove the question here. Never send pairing codes, serial numbers or passwords.

## Blocking an experiment

1. **Pump adapters (F1.3):** which Wi-Fi adapter is on each indoor unit (model on the module behind the front panel, e.g. MAC-577IF2-E), and which identifier belongs to which unit? Do you use MELCloud or MELCloud Home today, and do you want it to keep working?
2. **Cottage devices (F1.6):** are the heaters and sensors already commissioned into Google Home over Matter?
3. **Premium:** has the trial been activated, and does it say Premium or Premium Advanced? Home MCP needs Advanced.
4. **Stack (F1.7):** any preference between Python and TypeScript for the PC service?

## Spot-price automation

5. Are both sites on a spot-price contract? Does either site have a time-of-use transfer tariff (day/night)?
6. At home, how far may each pump drift from its comfort setpoint to save money: ±1 °C, more, or only in some rooms?
7. Should the home price automation also run in summer, when the pumps cool?
8. How far in advance do you usually know you are visiting the cottage, and roughly how long does it take to heat from 5–8 °C to 22 °C? Would a Google Calendar event (e.g. "Mökki") be a good way to start visit mode?

## Product

9. Who else uses the app: household members, guests? Should anyone have read-only access?
10. Is Tailscale on the phones and PC acceptable for remote access?
11. If the Google routes fall short for cottage automation, would a small cottage router (around €50–100, not a computer) be acceptable for the direct Matter route?
12. Do you want any notifications, for example a cottage device going offline or a visit preheat finishing? Through which channel?

## Settled

- Name Villa Kuppis; public repository [Vkuparin/villa-kuppis](https://github.com/Vkuparin/villa-kuppis) under Apache License 2.0. Finnish UI; technical docs in English.
- Service on the home Windows 11 PC, almost always on. Desktop browser and Android phones.
- No Home Assistant box, VM or cottage computer unless every other route fails.
- Cottage: Mill heaters and ALPSTUGA through Google Home over Matter, no Mill or IKEA cloud. Away 5–8 °C, visit 22 °C. Mill built-in frost protection; no water pipes. No custom outage handling.
- Premium usable during the six-month trial; each Premium feature needs a fallback.
- Pumps: three indoor units (LN25 on outdoor unit 1; LN35 and LN25 on outdoor unit 2), at home, independently operable.
- Spot-price automation is a high priority.
- Europe/Helsinki for both sites.
