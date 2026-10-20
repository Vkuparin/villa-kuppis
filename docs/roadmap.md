# Villa Kuppis roadmap

Release goals and product decisions live here. Canonical cards and statuses live in [task lists](tasks/README.md). Targets have no implied dates.

## Current release

No release is published yet. After publication, record the actual release link and validation evidence here.

## Delivery sequence

| Version | Goal | Canonical tasks | Release criterion |
| --- | --- | --- | --- |
| 0.1.0 | Prove the integrations | [0.1.0](tasks/0.1.0.md) | One pump reads and sets from the PC; price plans compute from fixed thresholds; the cottage reads and sets through the Home APIs; sensor-driven automations run a day's plan |
| 0.2.0 | Home: PC service, web app and pump automation | [0.2.0](tasks/0.2.0.md) | The pumps follow fixed price thresholds for cooling; home-away button and night mode work; graphs of readings; all settings changeable in the app |
| 0.3.0 | Cottage: Android app and automation | [0.3.0](tasks/0.3.0.md) | The cottage follows fixed thresholds through the daily upload; visit heating reaches its target in time |
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
- 2026-10-20: Cooling in the heating season: `Esijäähdytys käytössä` can be turned off so the target stays at the maximum and cooling does not compete with radiators. Optional power management by room temperature starts a unit above the maximum and stops it 0.5 °C below, only in cooling mode and never changing a mode.

## Decisions still needed

See [questions](questions.md). None block the current experiments.

## Working rules

Workflow rules are in [AGENTS.md](../AGENTS.md) and card rules in [tasks](tasks/README.md). The shared dashboard reads these files as they are.
