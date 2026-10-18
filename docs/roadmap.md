# Villa Kuppis roadmap

Release goals and product decisions live here. Canonical cards and statuses live in [task lists](tasks/README.md). Targets have no implied dates.

## Current release

No release is published yet. After publication, record the actual release link and validation evidence here.

## Delivery sequence

| Version | Goal | Canonical tasks | Release criterion |
| --- | --- | --- | --- |
| 0.1.0 | Prove the integrations | [0.1.0](tasks/0.1.0.md) | One pump reads and sets locally from the PC; price plans compute from owner rules; the cottage is reachable through Google and a plan runs as automations |
| 0.2.0 | PC service, Finnish UI and cottage price automation | [0.2.0](tasks/0.2.0.md) | All three pumps controllable from desktop and phone; the cottage follows the owner's price rules; visit button works |
| 0.3.0 | Pump cooling automation, before summer 2027 | [0.3.0](tasks/0.3.0.md) | Price rules run for the pumps in cooling mode |
| Unassigned | Ideas without a release | [Later](tasks/later.md) | Owner scope and release assignment |

## Decisions

- 2026-10-17: Public project Villa Kuppis, Apache License 2.0, Finnish UI. Service on the home Windows 11 PC with desktop and Android access.
- 2026-10-17: Cottage hardware: NE200-Outdoor, Archer BE230, four Mill Invisible Gen 4 heaters, two IKEA ALPSTUGA sensors, Google Home Speaker (2026). No cottage computer.
- 2026-10-18: No Home Assistant box or VM; last resort only. The PC runs our own Python service.
- 2026-10-18: Mill heaters and ALPSTUGA go into Google Home over Matter. Direct Matter from the PC is a secondary research route.
- 2026-10-18: Premium is not used. The speaker includes Premium Standard; Home MCP needs Advanced and is US-only. The Android app uploads cottage plans as Google Home automations.
- 2026-10-18: Spot-price rules are owner-defined thresholds in c/kWh mapped to setpoints, applied per 15-minute period. Cottage base 5 °C; visit button sets 22 °C.
- 2026-10-18: Pumps first. Three indoor units on two outdoor units, used mainly for summer cooling. Both sites on spot-price contracts, no day/night tariff.

## Decisions still needed

See [questions](questions.md). F1.3 needs the pump app (MELCloud or MELCloud Home). F1.6 waits for the cottage devices.

## Working rules

Workflow rules are in [AGENTS.md](../AGENTS.md) and card rules in [tasks](tasks/README.md). The shared dashboard reads these files as they are.
