# Cottage without a computer

## Confirmed inventory

| Item | Count | Role / evidence |
| --- | --- | --- |
| TP-Link NE200-Outdoor | 1 | 5G connection; operator, public addressing and firmware unknown |
| TP-Link Archer BE230 | 1 | Wi-Fi router; hardware revision and firmware unknown |
| Mill Invisible Gen 4 Wi-Fi heaters | 4 | Candidate local REST control; individual models and API behavior to verify |
| IKEA ALPSTUGA | 2 | Matter-over-Thread air-quality sensors, including temperature; not Wi-Fi sensors |
| Google Home Speaker (2026) | 1 | Google lists a Thread 1.3 border router and Matter hub |
| Cottage computer | 0 | Explicit owner constraint for the initial design |

[IKEA's Finnish product page](https://www.ikea.com/fi/fi/p/alpstuga-ilmanlaaduntunnistin-aelylaite-50604187/) identifies ALPSTUGA as Matter over Thread. [Google's specifications](https://store.google.com/product/google_home_speaker_specs?hl=en-US) identify the speaker's border-router role. Confirm the purchased model and exposed sensor clusters when commissioned. The four heaters should not be assumed to have four independent external room sensors: there are only two ALPSTUGA devices.

## Mill: easier protocol, still needs a network path

Update from owner clarification: replicating Google Home's remote functionality is the goal. Evaluate the Google Home APIs route below before requiring a direct PC-to-cottage VPN. The routing requirements in this section apply to direct local REST access, not to control through Google's hub infrastructure.

[Mill's Gen 4 page](https://millnorway.com/product/mill-invisible-smart-wifi-panel-heater-1200w/) advertises a local REST API. Use it as the first cottage control candidate instead of requiring remote Matter control for the heaters. Verify authentication, supported endpoints, temperature ranges, schedule persistence and whether onboarding needs the Mill cloud. Do not treat Gen 3 library support as verified Gen 4 compatibility.

The [Home Assistant Mill integration](https://www.home-assistant.io/integrations/mill/) documents local access for Gen 3; inspect and test its library against Gen 4 before reuse. Matter support may be another route for the exact heater, but it does not solve the WAN routing problem.

## Network feasibility gate

1. Determine whether the NE200 is in router or IP-passthrough mode and whether the BE230 is routing or acting as an access point. [TP-Link documents NE200 IP passthrough](https://www.tp-link.com/ca/support/faq/4752/). Passing through an operator-private address does not remove carrier-grade NAT.
2. Confirm the 5G operator/APN, public IPv4 availability, IPv6 prefix and inbound restrictions. Use non-overlapping private subnets at home and cottage. Keep actual addresses in private configuration.
3. Verify WireGuard support on the exact BE230 hardware/firmware. [TP-Link's guide](https://www.tp-link.com/ae/support/faq/3772/) covers client and server modes, but a consumer VPN client may only forward selected LAN devices' outbound traffic. It does not establish arbitrary incoming access to the cottage LAN or routed IPv6.
4. If cottage WAN is publicly reachable, test a tightly restricted VPN server on the cottage router and connect the PC as a client. If cottage is behind CGNAT, investigate an outbound tunnel to a reachable home endpoint or relay. This requires testing return routes, firewall policy and LAN reachability, not merely seeing “connected” in the VPN UI.
5. First prove PC-to-heater REST requests in both directions. Then test reconnection after router and 5G restarts. Do not expose heater HTTP ports to the internet.
6. Independently test Matter IPv6 routing and discovery. IPv4 REST working does not imply Matter will work.

No claim is made that the existing routers support a complete site-to-site solution. Their actual firmware behavior is a blocker to settle early, before writing a remote cottage controller.

## ALPSTUGA and Google hub boundary

A Thread border router routes IP traffic between Thread and the LAN; a Matter controller owns fabric credentials and device subscriptions. Google Home's fabric does not automatically grant our PC access. Multi-admin commissioning may add our controller to a device, but does not provide an internet transport to it.

[Matter deployment guidance](https://www.home-assistant.io/integrations/matter/) calls for local IPv6 and mDNS. Discovery, commissioning, routable addresses, return routes, subscriptions and recovery all need to work. Ordinary VPNs do not automatically carry link-local multicast. Do not assume an mDNS reflector alone solves the problem, or that the Google speaker exposes a generic REST API for all its devices.

With no cottage computer, dependable Google-independent ALPSTUGA readings on the distant PC are **unproven**. This restriction does not apply in the same way when reusing Google's remote-access infrastructure.

## Google Home APIs: first candidate after clarification

The owner clarified that the app should replicate the cottage's Google Home remote-control functionality. [Google's Home APIs connectivity documentation](https://developers.home.google.com/apis/android/connectivity) explicitly discusses third-party controllers on remote networks and supported hubs enabling remote Matter access. A custom mobile app can therefore investigate this supported route instead of reproducing Google's network transport or tunneling Matter across sites.

The candidate path is: Villa Kuppis Android app → Google Home APIs / Google infrastructure → online cottage Google hub → Matter devices. The owner accepts this Google dependency and confirms all household mobile devices are Android. This can avoid individual device manufacturers' clouds for Matter control, but does not route all devices directly to the PC.

[The documented standard Home APIs platforms](https://developers.home.google.com/apis) are Android/Kotlin and iOS/Swift. Home MCP is retained only as an optional trial experiment: the owner requires the production architecture to avoid Premium. See [platform decision](platform-decision.md). No free general PC API has been established. Google Home for web existing does not establish a public web SDK. Do not rely on a background phone bridge or emulator as an unattended production server without a separate design and proof.

First spike: use Google's official sample on the owner's phone platform, obtain appropriate user permissions, enumerate the cottage structure and actual device traits, read an ALPSTUGA temperature and perform an authorized Mill setpoint/read-back test over mobile data. Verify stale/offline behavior and exact exposed functions. [Supported Android device types](https://developers.home.google.com/apis/android/supported-device-types) include thermostat and temperature-sensor types; this is not a guarantee of every device feature. Check development-account and distribution requirements before choosing this route.

Cost research, 17 October 2026: no Home APIs subscription or per-call price was found in the reviewed official setup documentation. Budget zero API fees provisionally; this is not an explicit pricing guarantee. [OAuth setup](https://developers.home.google.com/apis/android/oauth) permits testing/use without Home Developer Console registration and documents a 100-user limit before registration. A Google Cloud project and OAuth credentials are still needed. Separate hosting, app-store distribution and optional paid services must not be confused with Home APIs usage. Validate the personal-account setup before committing to the integration; publishing the source repository does not itself require distributing the app through an app store.

In parallel, establish how the required desktop interface and PC-side automations would access cottage functions. Until that is solved, this is a promising mobile integration, not a complete solution to all requested platforms. Do not confuse Cloud-to-cloud device-provider integration or Nest-specific Device Access with general access to the user's Matter devices.

Sample-app setup detail: [Google's current authorization guide](https://developers.home.google.com/apis/android/sample-app/authorization) says the sample defaults to the partner-gated `home.platform.v2` scope. For the personal prototype, use the documented standard `home.platform.selected.devices` scope by selecting `HOME_PLATFORM_SCOPE_VERSION_1`. Add household accounts as OAuth test users. The sample's stock controls do not include every supported SDK device type, so thermostat and temperature-reading UI may need to be added. The guide currently says Developer Console registration is unavailable; public production distribution must be assessed separately from a household test app.

## Choices if the router test fails

| Choice | Consequence |
| --- | --- |
| Keep exact hardware and no computer | Defer unsupported cottage functions; accept vendor/Google access only if the owner agrees |
| Replace/add networking with verified routing support | May make Mill REST reachable; Matter still requires its own proof |
| Add a small local controller later | Most robust route for local Matter and automation; explicitly outside the initial constraint |

Do not quietly select the third choice. A router capable of running arbitrary software is also an added compute dependency and must be discussed honestly.

## Offline behavior

Each heater must regulate from its own sensor and retain an appropriate locally tested setting during PC/WAN outages. ALPSTUGA is monitoring-only initially. Verify power-restoration behavior, local schedules and manual controls on actual heaters. Remote alerts cannot be delivered without a communications path; an unavailable sensor must never be displayed as a current temperature. If frost protection is required, define it independently of this remote app.
