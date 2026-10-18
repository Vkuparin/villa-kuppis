# Mitsubishi LN25 / LN35

## Installation

At the main residence, controlled from the home PC over the home LAN.

| Indoor unit | Outdoor unit | Owner-supplied identifier |
| --- | --- | --- |
| LN25 | Outdoor unit 1 (single split) | To match |
| LN35 | Outdoor unit 2 (shared) | To match |
| LN25 | Outdoor unit 2 (shared) | To match |

Supplied identifiers: `LN25VGHZ2LU`, `LN35VG2WLU`, `LN25VG2WLU`, one per indoor unit. Match each to its unit from the label. All three can be operated independently.

Two indoor units share outdoor unit 2. Multi-split outdoor units usually cannot heat in one room and cool in another at the same time. Check this on the real units; if so, the UI should not offer a mode that conflicts with the other unit on the same outdoor unit.

## Routes

Each indoor unit needs its own adapter. Try these in order.

| Route | Runtime | Notes |
| --- | --- | --- |
| Existing Wi-Fi adapter, local API | Local | [`pymitsubishi`](https://mynixos.com/nixpkgs/package/python313Packages.pymitsubishi) describes itself as local control for MAC-577IF2-E adapters. The [VG2 parts list](https://library.mitsubishielectric.co.uk/pdf/download_full/5101) names MAC-577IF2-E for MSZ-LN25VG2 variants. If the units have it, this needs no new hardware and MELCloud keeps working. Check the library's protocol and maintenance before relying on it. |
| Existing adapter, ECHONET Lite | Local | [ECHONET integration](https://github.com/scottyphillips/echonetlite_homeassistant) lists MAC-568IF-E with LN25VG2/LN35VG2; European adapter support unknown. |
| CN105 serial adapter, e.g. ESP32 with [ESPHome](https://esphome.io/components/climate/mitsubishi_cn105/) | Local | Strong fallback. Prefer a ready-made board. It takes the connector the Wi-Fi adapter uses, so it replaces MELCloud on that unit. An [owner report](https://github.com/echavet/MitsubishiCN105ESPHome/discussions/317) confirms MSZ-LN25VG2W and MSZ-LN35VG2W. The PC talks to it over the ESPHome native API. |
| MELCloud / MELCloud Home cloud API | Cloud | Works, but adds a cloud dependency. Temporary bridge only. |
| REA on the MELCloud app or adapter | — | If the existing adapter has a local endpoint that `pymitsubishi` does not cover, REA is the tool for finding it. |

## First functions

Power, mode (heat, cool, dry, fan), target temperature, room temperature, fan speed and availability. Vanes and extras come after these work.

The ESPHome CN105 component documents a 16–31 °C target range. The spot-price planner keeps setpoints inside whatever range the chosen route reports.

## First experiment

1. Read the Wi-Fi adapter model from one indoor unit (behind the front panel) and note whether it uses MELCloud or MELCloud Home.
2. If it is MAC-577IF2-E, try `pymitsubishi` read-only from the PC. Otherwise check ECHONET Lite, then choose a CN105 board.
3. Compare readings with the remote control.
4. Change the setpoint by one degree, confirm the read-back, then restore it.
5. Change something with the physical remote and confirm the app sees it.

Done when one unit reads and sets reliably. Then repeat for the other two.
