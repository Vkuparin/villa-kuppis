# Villa Kuppis roadmap

Release goals and product decisions live here. Canonical cards and statuses live in [task lists](tasks/README.md). Targets have no implied dates.

## Current release

No release is published yet. After publication, record the actual release link and validation evidence here.

## Delivery sequence

| Version | Goal | Canonical tasks | Release criterion |
| --- | --- | --- | --- |
| 0.1.0 | Prove the integrations | [0.1.0](tasks/0.1.0.md) | One pump reads and sets locally from the PC; cottage access through Google is tested; spot prices are fetched and planned |
| 0.2.0 | Finnish UI and PC service with manual control | [0.2.0](tasks/0.2.0.md) | All three pumps and the cottage heaters controllable from desktop and phone |
| 0.3.0 | Spot-price automation | [0.3.0](tasks/0.3.0.md) | Price plans run for the pumps and the cottage away band; visit mode preheats to 22 °C |
| Unassigned | Ideas without a release | [Later](tasks/later.md) | Owner scope and release assignment |

## Decisions

- 2026-10-17: Public project Villa Kuppis, Apache License 2.0, Finnish UI. Service on the home Windows 11 PC with desktop and Android access.
- 2026-10-17: Cottage hardware: NE200-Outdoor, Archer BE230, four Mill Invisible Gen 4 heaters, two IKEA ALPSTUGA sensors, Google Home Speaker (2026). No cottage computer.
- 2026-10-17: Mitsubishi pumps are at home. Google dependency is accepted for the cottage; all household phones are Android.
- 2026-10-18: No Home Assistant box or VM; last resort only. The PC runs our own service.
- 2026-10-18: Mill heaters and ALPSTUGA go into Google Home over Matter. Direct Matter from the PC is a secondary research route.
- 2026-10-18: Premium may be used during the trial; each Premium feature needs a fallback. Home MCP is the primary PC route to the cottage while it lasts.
- 2026-10-18: Cottage away band 5–8 °C, visit 22 °C. Mill built-in frost protection; no frost or outage work in the app.
- 2026-10-18: Three indoor units on two outdoor units. Spot-price automation is a high priority.

## Decisions still needed

See [questions](questions.md). F1.3 needs the pump adapter models. F1.6 needs the cottage devices in Google Home. F1.7 needs a stack choice.

## Working rules

Workflow rules are in [AGENTS.md](../AGENTS.md) and card rules in [tasks](tasks/README.md). The shared dashboard reads these files as they are.
