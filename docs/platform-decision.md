# Client and device access decision

Status: decided by the owner, 20 October 2026, revision 6 (two-site Home Assistant). Supersedes revision 5 (Android app and Google Home automations), kept in summary below.

## Decided

- **One Home Assistant per site** as the device gateway; Villa Kuppis stays the one UI and planner. The owner's reasons: one device layer per site is architecturally cleaner, and more home IoT devices can be added through Home Assistant while Villa Kuppis remains the shared interface.
- **Pumps:** Home Assistant's [MELCloud Home integration](https://www.home-assistant.io/integrations/melcloud_home) on the home Home Assistant Container, after a parity test against today's `aiomelcloudhome` client (H1.3). If Home Assistant cannot keep the cooling-only, never-change-mode semantics, the direct client stays for the pumps while home Home Assistant takes future devices.
- **Cottage:** Mill Gen 4 heaters and ALPSTUGA sensors on the cottage Home Assistant OS through its [Matter Server](https://www.home-assistant.io/integrations/matter). Devices are commissioned into Google Home and shared to Home Assistant (Matter multi-admin), so the Google speaker provides Thread; Home Assistant uses third-party border routers only to reach the Thread network.
- **Clients:** the Villa Kuppis web app in a browser, optionally as a PWA. No Kotlin app, APK, Google Cloud project or OAuth test users.
- **Between sites:** Tailscale from the home server to the cottage Home Assistant API. No cross-site Matter fabric, routed multicast or forwarded ports.

## How each device is reached

| Job | Where |
| --- | --- |
| Prices, forecast, learning, optimizer, plans, history, settings | Villa Kuppis on the home server |
| Reading and commanding the pumps | Home Home Assistant (direct client until H1.5) |
| Reading and commanding the cottage | Cottage Home Assistant, through its API over Tailscale |
| Keeping the cottage safe when the link is down | Cottage Home Assistant's local controller and watchdog |
| Manual control | Villa Kuppis; in an emergency Home Assistant's own UI, Google Home or MELCloud Home |

## Gates before relying on it

- **Cottage commissioning (H2.1):** one heater and one sensor first. Check the heater's real minimum setpoint over Matter (is 5 °C accepted?), which ALPSTUGA readings are exposed, and how the Google speaker's Thread routing behaves with the WAN down. Add the other four devices only after that passes.
- **Mill caveat:** a Mill Gen 4 heater runs either Matter or Mill Cloud, not both; going back to Mill Cloud needs a reset on the heater. Home Assistant's Mill cloud integration is the fallback only if Matter lacks a critical control.
- **Pump parity (H1.3):** all three units, independent changes, read-back, latency and the known HTTP-500-after-success behaviour. Only one automation writer at a time during the cutover.
- **Thread fallback:** if the Google speaker does not work as a border router for Home Assistant, choose a supported local Thread border router then; do not pre-purchase radios.

## Superseded: Android and Google Home route

Revision 5 (18 October 2026) assumed the PC cannot reach Google Home, so a native Android app read the cottage through the Google Home APIs and uploaded the PC's plan once a day as sensor-driven Google Home automations: one per room, run every 30 minutes, at most 12 time windows a day, within Google's documented limits of 64 automations and 128 runs a day per home. Several capabilities it needed were undocumented (setting a Mill heater's absolute setpoint from an automation, reading the ALPSTUGA in a condition, background jobs, permission expiry of an unpublished app). The always-on cottage Home Assistant removes the need for all of them. Home MCP (Premium Advanced, US-only) and Mill cloud mode for the cottage were rejected earlier; direct Matter from the home PC across the internet stays out of scope.
