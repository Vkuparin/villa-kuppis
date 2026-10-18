# Mitsubishi LN25 / LN35

## Installation

At home. Not in Google Home.

| Indoor unit | Outdoor unit |
| --- | --- |
| LN25 | Outdoor unit 1 (single split) |
| LN35 | Outdoor unit 2 (shared) |
| LN25 | Outdoor unit 2 (shared) |

Supplied identifiers, one per indoor unit: `LN25VGHZ2LU`, `LN35VG2WLU`, `LN25VG2WLU`. All three can be operated independently. Two indoor units share outdoor unit 2. Multi-split outdoor units usually cannot heat one room and cool another at the same time; check this on the real units.

**Main use: summer cooling.** The apartment's radiators are geothermal and outside the owner's control.

## Route: MELCloud Home cloud

The owner uses the MELCloud Home app, so the units have MAC-597 Wi-Fi adapters. Mitsubishi says these need continuous internet; there is no known local API, and `pymitsubishi` does not support them.

The PC controls them through the MELCloud Home cloud, with the same account the app uses:

- [`aiomelcloudhome`](https://github.com/erwindouna/aiomelcloudhome): async Python client for buildings, devices, air-to-air control and measurements, with a realtime websocket stream and polling as fallback.
- [`melcloudhome`](https://github.com/andrew-blake/melcloudhome): a Home Assistant integration with its own client, reverse engineered from the MELCloud Home web app. A second reference if the first lacks something.

Both are unofficial. No new hardware is needed, and the MELCloud Home app keeps working alongside.

**Local alternative, only if the cloud proves unreliable:** replace each MAC-597 with a CN105 serial board running [ESPHome](https://esphome.io/components/climate/mitsubishi_cn105/). An [owner report](https://github.com/echavet/MitsubishiCN105ESPHome/discussions/317) confirms MSZ-LN25VG2W and MSZ-LN35VG2W. This takes the adapter's connector, so MELCloud Home stops working on that unit.

## First functions

Power, mode (cool, heat, dry, fan), target temperature, room temperature, fan speed and availability. Vanes come after these work. The spot-price planner keeps setpoints inside the range the unit reports.

## First experiment

1. With the owner's go-ahead, sign in from the PC with `aiomelcloudhome` and list the three units. Keep credentials and device IDs in `scratch/`.
2. Read one unit's state and compare it with the MELCloud Home app.
3. Change its setpoint by one degree, confirm the read-back, then restore it.
4. Change something in the MELCloud Home app and confirm the PC sees it.

Done when one unit reads and sets reliably. Then check the other two.
