# Mitsubishi LN25 / LN35

## Installation

At home, controlled from the home PC over the home LAN (Google Nest Wifi Pro). Not in Google Home.

| Indoor unit | Outdoor unit |
| --- | --- |
| LN25 | Outdoor unit 1 (single split) |
| LN35 | Outdoor unit 2 (shared) |
| LN25 | Outdoor unit 2 (shared) |

Supplied identifiers, one per indoor unit: `LN25VGHZ2LU`, `LN35VG2WLU`, `LN25VG2WLU`. All three can be operated independently. Two indoor units share outdoor unit 2. Multi-split outdoor units usually cannot heat one room and cool another at the same time; check this on the real units.

**Main use: summer cooling.** The apartment's radiators are geothermal and outside the owner's control, so pump heating matters little.

## Wi-Fi adapter

The MSZ-LN series has a built-in Wi-Fi interface. Mitsubishi's documents name several possible adapters:

- The [MSZ-LN Wi-Fi interface manual](https://manuals.plus/m/95adb8ddd1845ae199f40ce676850ec07b1c22de56040e7d4ee2426083cf058b.pdf) covers MAC-567IFB-E, MAC-567IF-E and MAC-587IF-E.
- The [VG2 parts list](https://library.mitsubishielectric.co.uk/pdf/download_full/5101) names MAC-577IF2-E for MSZ-LN25VG2 variants.
- Newer units with the MELCloud Home app use MAC-597IF-E.

The docs cannot say which one these units have. Two checks avoid opening the units:

1. **Which app do you use: MELCloud or MELCloud Home?** MELCloud Home points to MAC-597, which `pymitsubishi` reportedly does not support.
2. **Try it.** Find the adapters' addresses in the Google Home app's Wi-Fi device list, then run a read-only `pymitsubishi` status call from the PC. If it answers, the route works.

The adapters use 2.4 GHz Wi-Fi only.

## Routes

Each indoor unit needs its own working adapter. Try these in order.

| Route | Runtime | Notes |
| --- | --- | --- |
| Existing adapter with [`pymitsubishi`](https://github.com/pymitsubishi/pymitsubishi) | Local | Python, MIT licence. Uses the adapter's local `/smart` HTTP endpoint. Built for MAC-577IF2-E; MAC-587 reported working; MAC-597 reportedly not. No new hardware, and MELCloud keeps working. A [Home Assistant user report](https://github.com/pymitsubishi/homeassistant-mitsubishi/issues/61) mentions occasional timeouts, so use retries. |
| CN105 serial adapter, e.g. ESP32 with [ESPHome](https://esphome.io/components/climate/mitsubishi_cn105/) | Local | Fallback. Prefer a ready-made board. It takes the Wi-Fi adapter's connector, so it replaces MELCloud on that unit. An [owner report](https://github.com/echavet/MitsubishiCN105ESPHome/discussions/317) confirms MSZ-LN25VG2W and MSZ-LN35VG2W. The PC talks to it with `aioesphomeapi`. |
| MELCloud / MELCloud Home cloud API | Cloud | Temporary bridge only. |
| REA on the adapter or the MELCloud app | — | Only if the adapter answers locally but `pymitsubishi` lacks a needed function. The [adapter analysis repo](https://github.com/pymitsubishi/mac-577if-e) is a starting point. |

## First functions

Power, mode (cool, heat, dry, fan), target temperature, room temperature, fan speed and availability. Vanes come after these work. The spot-price planner keeps setpoints inside the range the unit reports.

## First experiment

1. Check which app you use and find the three adapters' LAN addresses. Keep the addresses in `scratch/`.
2. From the PC, read one unit's status with `pymitsubishi` and compare it with the remote control.
3. Change the setpoint by one degree, confirm the read-back, then restore it.
4. Change something with the remote and confirm the read sees it.

Done when one unit reads and sets reliably. Then repeat for the other two. If step 2 fails, choose a CN105 board.
