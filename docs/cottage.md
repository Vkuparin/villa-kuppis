# Cottage

## Inventory

| Item | Count | Role |
| --- | --- | --- |
| TP-Link NE200-Outdoor | 1 | 5G connection |
| TP-Link Archer BE230 | 1 | Wi-Fi router |
| Mill Invisible Gen 4 Wi-Fi heaters | 4 | Matter over Wi-Fi, commissioned into Google Home |
| IKEA ALPSTUGA | 2 | Matter-over-Thread air-quality sensors with temperature |
| Google Home Speaker (2026) | 1 | Matter hub and Thread border router ([specs](https://store.google.com/product/google_home_speaker_specs?hl=en-US)) |
| Cottage computer | 0 | None planned |

The Mill manuals offer either Mill cloud or Matter. The owner chose Matter through Google Home, so the Mill app and cloud are not part of the design. ALPSTUGA is added to Google Home directly, without the IKEA app.

## Heating policy

- **Away:** keep the cottage at 5–8 °C. The spot-price planner chooses where in that band to sit; see [spot-price automation](spot-price.md).
- **Visit:** raise the heaters to 22 °C before arrival.
- **Fallback:** the heaters keep their last setpoint if anything upstream fails, and have built-in frost protection that does not depend on Wi-Fi. There are no water pipes.

Check early that Google exposes 5 °C and 22 °C as valid setpoints for these heaters. Matter thermostats report their own minimum and maximum, and the minimum might be above 5 °C.

## Access routes

| Route | Manual control | PC automation | Cost | Status |
| --- | --- | --- | --- | --- |
| Google Home app on Android | Yes | No | Free | Works once devices are commissioned |
| [home.google.com](https://home.google.com) | Yes, from desktop | No | Free | Has had device controls, including thermostats, since September 2025; check Mill and ALPSTUGA appear |
| Villa Kuppis Android app with [Home APIs](https://developers.home.google.com/apis/android/connectivity) | Yes | Through Google Home automations the app writes | Free | First experiment |
| Google Home MCP from the PC | Yes | Yes | Premium Advanced | Usable during the trial |
| Direct Matter from the PC | Yes | Yes | Possibly a cottage router | Secondary research, below |

The PC needs a route for spot-price automation. Use Home MCP while Premium lasts. The fallback is in [platform decision](platform-decision.md).

## Home APIs setup notes

Start from [Google's Android sample app](https://github.com/google-home/google-home-api-sample-app-android). Its [authorization guide](https://developers.home.google.com/apis/android/sample-app/authorization) defaults to the partner-gated `home.platform.v2` scope; for a personal app, select `HOME_PLATFORM_SCOPE_VERSION_1` (`home.platform.selected.devices`). Add household accounts as OAuth test users. A Google Cloud project and OAuth credentials are needed; [unregistered apps allow 100 users](https://developers.home.google.com/apis/android/oauth). No usage fee was found in the documentation. The sample may lack thermostat and sensor screens; add them if so.

The Home APIs also include an Automation API, so the app can create and manage Google Home automations. Those automations run in Google Home without the PC or the phone.

## Secondary route: direct Matter from the home PC

The idea: let the PC talk to the heaters and sensors itself, through the cottage router. It is possible in principle, but reverse engineering is not the way in and port forwarding is not enough.

**Why reverse engineering does not help.** Matter is an open standard with open-source controllers ([connectedhomeip](https://github.com/project-chip/connectedhomeip), [matter.js](https://github.com/project-chip/matter.js)). Nothing in the protocol is hidden. Each session is encrypted with keys held by the controller's fabric, so captured Google traffic cannot be replayed or reused. The PC has to become a Matter controller in its own right.

**What it takes:**

1. **Join a second fabric (multi-admin).** In Google Home, a device can be shared with another Matter app; Google generates a pairing code. Commissioning finds the device by mDNS on the local network, so this step happens at the cottage: run the controller on a laptop there, then move its fabric storage to the home PC.
2. **Reach the devices over IPv6.** Matter runs over IPv6 on UDP port 5540. The Mill heaters have IPv6 addresses on the cottage Wi-Fi. The ALPSTUGA sensors sit on the Thread network behind the speaker, which advertises their route only on the cottage LAN. An IPv4 port forward on the router cannot reach either.
3. **Carry that across sites.** The PC needs a routed IPv6 tunnel to the cottage LAN that includes the Thread prefix. It also needs to find devices without mDNS, which does not cross a VPN. Matter controllers normally rely on mDNS, so this needs fixed addresses or unicast DNS-SD.

**Likely verdict:** the NE200 and BE230 consumer firmware probably cannot do step 3. The cheapest way to get it is a small router at the cottage that supports WireGuard with IPv6 routing (for example an OpenWrt or GL.iNet model, roughly €50–100). That is a router, not a computer, but it is extra hardware. Pursue this only if the Google routes fall short.

If both Google and the PC control the same device, the last write wins. The app shows the device's reported state, so this is visible.
