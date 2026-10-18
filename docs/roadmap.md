# Villa Kuppis roadmap

Release goals and product decisions live here. Canonical cards and statuses live in [task lists](tasks/README.md). Targets have no implied dates.

## Current release

No release is published yet. After publication, record the actual release link and validation evidence here.

## Delivery sequence

| Version | Goal | Canonical tasks | Release criterion |
| --- | --- | --- | --- |
| 0.1.0 | Prove the integrations | [0.1.0](tasks/0.1.0.md) | One pump and one heater read and set from the PC; price plans compute from owner thresholds |
| 0.2.0 | PC service, Finnish web app and cottage price automation | [0.2.0](tasks/0.2.0.md) | All pumps and heaters controllable from desktop and phone; the cottage follows the owner's thresholds; visit button works |
| 0.3.0 | Pump cooling automation, before summer 2027 | [0.3.0](tasks/0.3.0.md) | Price thresholds run for the pumps in cooling mode |
| Unassigned | Ideas without a release | [Later](tasks/later.md) | Owner scope and release assignment |

## Decisions

- 2026-10-17: Public project Villa Kuppis, Apache License 2.0, Finnish UI. Service on the home Windows 11 PC with desktop and Android access.
- 2026-10-17: Cottage hardware: NE200-Outdoor, Archer BE230, four Mill Invisible Gen 4 heaters, two IKEA ALPSTUGA sensors, Google Home Speaker (2026). No cottage computer.
- 2026-10-18: No Home Assistant box or VM; last resort only. The PC runs our own Python service.
- 2026-10-18: Premium is not used; Home MCP needs Advanced and is US-only, and the speaker includes Standard.
- 2026-10-18: The goal is one app instead of many; take the simplest working route. Pumps through the MELCloud Home cloud. Heaters in Mill cloud mode, controlled from the PC; Matter through Google is the fallback. ALPSTUGA stays in Google Home. One web app for desktop and phone; no native Android app for now.
- 2026-10-18: Spot-price thresholds are owner-defined, compared against the raw spot price, applied per 15-minute period. Cottage base 5 °C; visit button sets 22 °C until pressed again.
- 2026-10-18: Pumps first, used mainly for summer cooling. Both sites on spot-price contracts.

## Decisions still needed

See [questions](questions.md). None block the current experiments.

## Working rules

Workflow rules are in [AGENTS.md](../AGENTS.md) and card rules in [tasks](tasks/README.md). The shared dashboard reads these files as they are.
