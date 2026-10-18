# Cottage

## Inventory

| Item | Count | Role |
| --- | --- | --- |
| TP-Link NE200-Outdoor | 1 | 5G connection |
| TP-Link Archer BE230 | 1 | Wi-Fi router |
| Mill Invisible Gen 4 Wi-Fi heaters | 4 | Matter over Wi-Fi ([CSA-certified](https://csa-iot.org/csa_product/mill-wi-fi-panel-heater-gen4-40/)), in Google Home |
| IKEA ALPSTUGA | 2 | Matter-over-Thread air-quality sensors with temperature, in Google Home |
| Google Home Speaker (2026) | 1 | Matter hub and Thread border router ([specs](https://store.google.com/product/google_home_speaker_specs?hl=en-US)) |
| Cottage computer | 0 | None planned |

The devices arrive early November 2026.

## Rooms

| Room | Heaters | Sensor |
| --- | --- | --- |
| Large room | 2 × 1050 W, 1 × 500 W | ALPSTUGA |
| Small room | 1 × 1050 W | ALPSTUGA |

## Setup when the devices arrive

1. Commission the four heaters and two sensors into Google Home over Matter, with the speaker. Use Matter mode on the heaters, not Mill cloud.
2. Assign each to its room in Google Home.
3. Check in the Google Home app that each heater accepts 5, 12 and 22 °C.
4. Run [experiments A and B](integration-experiments.md).

## Heating

- **Maintenance heating:** price rules or the optimizer, base around 5 °C. See [spot-price automation](spot-price.md).
- **Visit:** in the Android app, set the arrival time and target (e.g. Friday 18:00, 22 °C). The app plans the cheapest heat-up that reaches the target in time. The end-visit button returns to maintenance heating.
- **Control:** the heaters follow each room's ALPSTUGA through sensor-driven Google Home automations; see [platform decision](platform-decision.md#sensor-driven-automations).
- **Fallback:** if the PC or phone is down, the uploaded automations keep running, and the Google Home app still controls everything manually. The heaters have built-in frost protection. There are no water pipes.

## Not pursued: direct Matter from the PC

Controlling the devices from the PC directly would need a routed IPv6 tunnel into the cottage network, including the Thread network behind the speaker. The current gear does not look able to provide it:

- The BE230 lists a WireGuard server, but nothing confirms it routes IPv6, and a [community report](https://community.tp-link.com/en/business/forum/topic/843612) describes unstable WAN IPv6.
- 5G connections are usually behind carrier-grade NAT, which blocks incoming connections.

Reverse engineering would not help: Matter is an open standard, and its sessions are encrypted with keys only the controlling app holds.
