# Open questions

Answers go into the relevant design doc; then remove the question here. Never send pairing codes, serial numbers or passwords.

## Blocking an experiment

1. **Pumps (F1.3):** do you use MELCloud or MELCloud Home for the pumps today? That decides whether `pymitsubishi` is likely to work.
2. **Cottage bridge:** is there a spare Android phone that could stay at home on a charger and do the daily plan upload? A personal phone also works but is less dependable.

## Spot-price rules

3. Should price thresholds include VAT (25.5 %)? Finnish price apps usually show prices with VAT. Should the contract margin be added?
4. Besides fixed thresholds, do you want the "cheapest N quarter-hours of the day" rule type, for winter days when prices never get very low?
5. Should visit mode end only when you press the button, or also automatically after a set number of days?
6. For summer cooling: roughly which rules do you have in mind (comfort temperature, precooling when cheap, off when expensive)? This can wait until spring.

## Fallbacks

7. If the Automation API cannot carry a day's plan, would you accept Mill cloud for the heaters instead of Matter? The PC could then control them directly, and the ALPSTUGA sensors would stay in Google Home.

## Product

8. Who else uses the app: household members, guests? Should anyone have read-only access?
9. Do you want notifications, for example a cottage device going offline or an upload failing? Through which channel?

## Settled

- Name Villa Kuppis; public repository [Vkuparin/villa-kuppis](https://github.com/Vkuparin/villa-kuppis) under Apache License 2.0. Finnish UI; technical docs in English.
- Python service on the home Windows 11 PC, almost always on (coordinator choice; owner may override). Native Android app and desktop browser UI. Tailscale for phone access, free plan.
- No Home Assistant box, VM or cottage computer unless every other route fails.
- No Premium in the design: the speaker includes Premium Standard, and Home MCP needs Advanced and is US-only.
- Cottage: Mill heaters and ALPSTUGA through Google Home over Matter, arriving early November 2026. Owner-defined price rules with a 5 °C base; visit button sets 22 °C. Mill built-in frost protection; no water pipes. Cottage network stays NE200-Outdoor and Archer BE230.
- Home: three indoor units (LN25 on outdoor unit 1; LN35 and LN25 on outdoor unit 2), not in Google Home. Summer cooling matters most; radiators are geothermal and outside our control. Home network is a Google Nest Wifi Pro.
- Both sites on spot-price contracts, no day/night tariff. Spot-price automation is a high priority.
- Europe/Helsinki for both sites.
