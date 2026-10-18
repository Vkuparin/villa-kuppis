# Cottage

## Inventory

| Item | Count | Role |
| --- | --- | --- |
| TP-Link NE200-Outdoor | 1 | 5G connection |
| TP-Link Archer BE230 | 1 | Wi-Fi router |
| Mill Invisible Gen 4 Wi-Fi heaters | 4 | Mill cloud mode; controlled from the PC |
| IKEA ALPSTUGA | 2 | Matter-over-Thread air-quality sensors with temperature, in Google Home |
| Google Home Speaker (2026) | 1 | Thread border router and Matter hub for ALPSTUGA ([specs](https://store.google.com/product/google_home_speaker_specs?hl=en-US)) |
| Cottage computer | 0 | None planned |

The devices arrive early November 2026. See [platform decision](platform-decision.md) for why the heaters use Mill cloud rather than Matter.

## Setup when the devices arrive

1. Set up one heater in the Mill app in Mill cloud mode, on the BE230 Wi-Fi.
2. From the home PC, confirm `millheater` lists it, reads its temperature and setpoint, and sets 5, 12 and 22 °C. See [experiment A](integration-experiments.md#a-mill-heater-through-mill-cloud).
3. If that works, set up the other three heaters the same way. If not, use the Matter fallback in [platform decision](platform-decision.md#fallback-matter-through-google).
4. Add the two ALPSTUGA sensors to Google Home with the speaker.

## Heating policy

- **Away:** owner-defined spot-price thresholds with a base of 5 °C. For example, heat to 12 °C when the spot price is below 5 c/kWh. See [spot-price automation](spot-price.md).
- **Visit:** a button in the app sets 22 °C and suspends the price rules until the owner presses the end button. Heat-up is estimated at 12 hours or more.
- The heaters keep their last setpoint if anything upstream fails, and have built-in frost protection. There are no water pipes.

## Not pursued: direct Matter from the PC

Controlling Matter devices from the PC directly would need a routed IPv6 tunnel into the cottage network, including the Thread network behind the speaker. The current gear does not look able to provide it:

- The BE230 lists a WireGuard server, but nothing confirms it routes IPv6, and a [community report](https://community.tp-link.com/en/business/forum/topic/843612) describes unstable WAN IPv6.
- 5G connections are usually behind carrier-grade NAT, which blocks incoming connections.

With the heaters on Mill cloud, this would only matter for the ALPSTUGA sensors. Reverse engineering would not help: Matter is an open standard, and its sessions are encrypted with keys only the controlling app holds.
