# Villa Kuppis roadmap

Release goals and product decisions live here. Canonical cards and statuses live in [task lists](tasks/README.md). Targets have no implied dates.

## Current release

No release is published yet. After publication, record the actual release link and validation evidence here.

## Delivery sequence

| Version | Goal | Canonical tasks | Release criterion |
| --- | --- | --- | --- |
| 0.1.0 | Prove the integrations | [0.1.0](tasks/0.1.0.md) | One pump reads and sets from the PC; price plans compute from fixed thresholds; the cottage devices are commissioned (the cottage proof itself is H2.1 in 0.3.0) |
| 0.2.0 | Home: PC service, web app and pump automation | [0.2.0](tasks/0.2.0.md) | The pumps follow fixed price thresholds for cooling; home-away button and night mode work; graphs of readings; all settings changeable in the app |
| 0.3.0 | Two-site Home Assistant and cottage control | [0.3.0](tasks/0.3.0.md) | Villa Kuppis runs on the always-on home server with the pumps through Home Assistant (or the direct client as fallback); the cottage Home Assistant follows room targets locally and keeps safe heat with the WAN or home server down; fixed thresholds and visit heating work; browser or PWA only, no APK |
| 0.4.0 | Optimizer for both sites | [0.4.0](tasks/0.4.0.md) | Smart maintenance heating, optimized visits and cooling optimization run from learned room rates and the FMI forecast |
| Unassigned | Ideas without a release | [Later](tasks/later.md) | Owner scope and release assignment |

## Decisions

- 2026-10-17: Public project Villa Kuppis, Apache License 2.0, Finnish UI. Service on the home Windows 11 PC.
- 2026-10-17: Cottage hardware: NE200-Outdoor, Archer BE230, four Mill Invisible Gen 4 heaters, two IKEA ALPSTUGA sensors, Google Home Speaker (2026). No cottage computer.
- 2026-10-18: No Home Assistant box or VM; last resort only. The PC runs our own Python service.
- 2026-10-18: No Premium. The speaker includes Premium Standard; Home MCP needs Advanced and is US-only. The PC therefore cannot reach Google Home.
- 2026-10-18: Pumps through the MELCloud Home cloud; all three units are in MELCloud Home.
- 2026-10-18: Cottage devices in Google Home over Matter, so the heaters can follow the ALPSTUGA readings. The native Android app is the cottage link: it reports readings to the PC and uploads the PC's plans as sensor-driven Google Home automations once a day, plus a re-send button.
- 2026-10-18: Spot-price automation: the raw spot price, fixed thresholds first, then a smart maintenance heating optimizer; settings per site or per room. Visit heating plans the cheapest heat-up to a target by an arrival time.
- 2026-10-18: Household members use the app with full control, without an app login. Android notifications. Time-series graphs of all readings.
- 2026-10-18: Pumps are used mainly for summer cooling; both sites are on spot-price contracts.
- 2026-10-18: Home ships first, because the pumps are available now. Cooling defaults: maximum 25 °C, precooling 21 °C, switching off allowed with a minimum off time; separate home-away button; quiet night fan in chosen rooms. Every setting is changeable in the app.
- 2026-10-18: No season mode: automations act on measured room temperatures; pump automation is cooling-only and never changes a mode. Cottage automations fit Google's limits: one per room, every 30 minutes, at most 12 time windows a day.
- 2026-10-19: No pull requests: commit and merge directly to main in this small hobby project.
- 2026-10-19: Design phase finished; implementation starts with the offline spot-price experiment. Live account and device tests still need the owner's go-ahead.
- 2026-10-20: Home is installed as an unattended service with private Tailscale HTTPS access. UI preferences (dark theme, accent colour) are per browser. Settings save automatically and are explained in the app; settings of the other control kind are hidden.
- 2026-10-20: A master `Automaatio` switch in the page header turns the whole automation on or off; it starts off unless `"plan": true`, and switching off does not restore device settings. The plan loop and the daily price fetch always run in the installed service.
- 2026-10-20: VAT is an opt-in setting (25.5 %) that applies to the price curve, cost and the cooling plan's thresholds, so thresholds are entered with VAT while it is on.
- 2026-10-20: Two-site Home Assistant (owner's choice: one device layer per site is architecturally cleaner, and more home IoT devices can be added through Home Assistant while Villa Kuppis stays the shared interface; supersedes the 2026-10-18 Android and Google Home automation decisions and "no Home Assistant"). Two always-on x86-64 machines: home runs Debian + Docker Compose with Villa Kuppis, Home Assistant Container (MELCloud Home integration after a parity test; the direct client stays as rollback) and host Tailscale; the cottage runs HAOS with the Matter Server and Tailscale apps, controlling the Mill heaters and ALPSTUGA sensors locally with the Google speaker as Thread border router. Villa Kuppis stays the one UI and planner and sends current room targets with a heartbeat; the cottage Home Assistant keeps a local safe fallback. No Kotlin app, APK, Google Home APIs or automation uploads. Precedence: device safety, then manual override, then visit, then the economic plan.
- 2026-10-20: Hardware bought (not yet arrived): HP MP9 G4 mini-PC (Core i3-8100T, 8 GB, 256 GB NVMe) for the cottage with HAOS; HP OmniBook laptop (Ryzen AI 5 340, 16 GB, 512 GB NVMe) as the home server with Debian and Home Assistant in Docker.
- 2026-10-20 (evening): Both sites run Debian 13 as the host and Home Assistant OS as a KVM virtual machine on a wired bridge with its own LAN address (owner's server plan). This replaces Home Assistant Container at home and bare-metal HAOS at the cottage, keeps HAOS apps such as the Matter Server, and lets each host keep a 5 TB USB drive at `/srv/archive` for Villa Kuppis and Home Assistant backups. Villa Kuppis reaches the cottage VM through a Tailscale subnet route for its single address on `cottage-server`.
- 2026-10-20: Cooling in the heating season: `Esijäähdytys käytössä` can be turned off so the target stays at the maximum and cooling does not compete with radiators. Optional power management by room temperature starts a unit above the maximum and stops it 0.5 °C below, only in cooling mode and never changing a mode.

## Decisions still needed

See [questions](questions.md). None block the current experiments.

## Working rules

Workflow rules are in [AGENTS.md](../AGENTS.md) and card rules in [tasks](tasks/README.md). The shared dashboard reads these files as they are.
