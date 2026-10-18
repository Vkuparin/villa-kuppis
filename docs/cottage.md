# Cottage

## Inventory

| Item | Count | Role |
| --- | --- | --- |
| TP-Link NE200-Outdoor | 1 | 5G connection |
| TP-Link Archer BE230 | 1 | Wi-Fi router |
| Mill Invisible Gen 4 Wi-Fi heaters | 4 | Matter over Wi-Fi ([CSA-certified](https://csa-iot.org/csa_product/mill-wi-fi-panel-heater-gen4-40/), Matter 1.4) |
| IKEA ALPSTUGA | 2 | Matter-over-Thread air-quality sensors with temperature |
| Google Home Speaker (2026) | 1 | Matter hub and Thread border router ([specs](https://store.google.com/product/google_home_speaker_specs?hl=en-US)) |
| Cottage computer | 0 | None planned |

The devices arrive in about two weeks (early November 2026). The Mill manuals offer either Mill cloud or Matter; the owner chose Matter through Google Home. ALPSTUGA is added to Google Home directly, without the IKEA app.

## Heating policy

- **Away:** owner-defined spot-price rules, base 5 °C. For example, heat to 12 °C when the price is below 5 c/kWh. See [spot-price automation](spot-price.md).
- **Visit:** a button in the app sets 22 °C and suspends the price rules until the visit ends. Heat-up is estimated at 12 hours or more.
- **Fallback:** the heaters keep their last setpoint if anything upstream fails, and have built-in frost protection. There are no water pipes.

Check on commissioning that Google accepts 5 °C, 12 °C and 22 °C for these heaters. Matter thermostats report their own minimum and maximum.

## Access routes

| Route | Manual control | Price automation | Status |
| --- | --- | --- | --- |
| Google Home app on Android | Yes | No | Works once devices are commissioned |
| [home.google.com](https://home.google.com) | Yes, from desktop | No | Has had device controls, including thermostats, since September 2025 |
| Villa Kuppis Android app with the [Home APIs](https://developers.home.google.com/apis/android/connectivity) | Yes | Uploads the PC's daily plan as Google Home automations | First experiment |
| Direct Matter from the PC | Yes | Yes | Secondary research, below |

Home MCP is not available; see [platform decision](platform-decision.md).

## Home APIs setup notes

Start from [Google's Android sample app](https://github.com/google-home/google-home-api-sample-app-android). Its [authorization guide](https://developers.home.google.com/apis/android/sample-app/authorization) defaults to the partner-gated `home.platform.v2` scope; for a personal app, select `HOME_PLATFORM_SCOPE_VERSION_1` (`home.platform.selected.devices`). Add household accounts as OAuth test users. A Google Cloud project and OAuth credentials are needed; [unregistered apps allow 100 users](https://developers.home.google.com/apis/android/oauth). No usage fee was found. The sample may lack thermostat and sensor screens; add them if so.

The [Automation API](https://developers.home.google.com/apis/android/device/thermostat) supports thermostat elements and clock-time starters. Automations it creates run in Google Home without the phone or the PC.

## Secondary route: direct Matter from the home PC

The idea: let the PC talk to the heaters and sensors itself, through the cottage router. It is possible in principle, but reverse engineering is not the way in, and a port forward is not enough.

**Why reverse engineering does not help.** Matter is an open standard with open-source controllers ([connectedhomeip](https://github.com/project-chip/connectedhomeip), [matter.js](https://github.com/project-chip/matter.js), python-matter-server). Nothing in the protocol is hidden. Each session is encrypted with keys held by the controller's fabric, so captured Google traffic cannot be replayed. The PC has to become a Matter controller itself.

**What it takes:**

1. **Join a second fabric (multi-admin).** In Google Home, a device can be shared with another Matter app; Google generates a pairing code. Commissioning finds the device by mDNS on the local network, so this happens at the cottage: run the controller on a laptop there, then move its fabric storage to the home PC.
2. **Reach the devices over IPv6.** Matter runs over IPv6 on UDP port 5540. The Mill heaters have IPv6 addresses on the cottage Wi-Fi. The ALPSTUGA sensors sit on the Thread network behind the speaker, which advertises their route only on the cottage LAN. An IPv4 port forward cannot reach either.
3. **Carry that across sites.** The PC needs a routed IPv6 tunnel to the cottage LAN that includes the Thread prefix. It also has to find devices without mDNS, which does not cross a VPN.

**With the current gear:** the BE230 lists a WireGuard VPN server and IPv6 support. No source confirms routed IPv6 through its VPN, and one [community report](https://community.tp-link.com/en/business/forum/topic/843612) describes unstable WAN IPv6 on firmware 1.2.1. Behind the NE200, a 5G connection usually has carrier-grade NAT on IPv4, so the BE230 VPN server may not be reachable at all without a public-IP option from the operator.

**Likely verdict:** not workable on the current gear without testing that is hard to do remotely. Revisit only if the Android route fails. Then the options are a public IP from the 5G operator plus testing the BE230, or a small router with WireGuard and IPv6 routing at the cottage.

If both Google and the PC control the same device, the last write wins. The app shows the device's reported state, so this is visible.
