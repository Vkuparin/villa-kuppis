# Villa Kuppis roadmap

Release goals and product decisions live here. Canonical cards and statuses live in [task lists](tasks/README.md). Targets have no implied dates.

## Current release

No release is published yet. After publication, record the actual release link and validation evidence here.

## Delivery sequence

| Version | Goal | Canonical tasks | Release criterion |
| --- | --- | --- | --- |
| 0.1.0 | Prove one local Mitsubishi integration and resolve cottage connectivity | [Initial release](tasks/0.1.0.md) | One identified pump passes local read/write and outage checks; cottage access limits are documented |
| Unassigned | Ideas without a release | [Later](tasks/later.md) | Owner scope and release assignment |

## Accepted direction

- 2026-10-17: Public project named Villa Kuppis; Finnish UI; PC-hosted service with desktop and remote phone access.
- 2026-10-17: Mitsubishi LN25/LN35 is the first case study. Prefer a verified existing local protocol or adapter before new cloud emulation work.
- 2026-10-17: Cottage has NE200-Outdoor, Archer BE230, four Mill Invisible Gen 4 heaters, two IKEA ALPSTUGA sensors and Google Home Speaker (2026). No cottage computer initially.
- 2026-10-17: Research and documentation only for this setup. Architecture remains tentative and no live equipment has been changed.
- 2026-10-17: Owner clarified that Mitsubishi pumps are at the main residence and cottage functionality should replicate Google Home remote control. Google dependence is accepted and all household mobile devices are Android. Investigate official Android Home APIs first; desktop/server API access remains open.
- 2026-10-17: Research identified a documented Home MCP HTTP interface as a PC-access candidate. It is early access and requires Premium Advanced. Selection, eligibility, regional price and actual hardware support remain unverified; no subscription is authorized. See [platform decision](platform-decision.md) and [experiment specifications](integration-experiments.md).
- 2026-10-17: Owner confirmed Windows 11, normally powered on. Exact Mitsubishi model checks are deferred until available. The new speaker includes a six-month Premium trial, but production must avoid requiring Premium; MCP is only an optional trial experiment. Trial tier/activation have not been verified.
- 2026-10-17: Owner created the public GitHub repository and selected Apache License 2.0. Publishing the research scaffold is distinct from releasing a working application.
- 2026-10-17: Research scaffold published as c3124bf and its complete file tree verified. Owner supplied Mitsubishi identifiers LN25VGHZ2LU, LN35VG2WLU and LN25VG2WLU; match them to actual units/adapters before selecting hardware.

## Decisions still needed

See [questions](questions.md). F2.1 waits for exact pump/adapter identity and installation preference. F2.2 needs a PC transport that does not require Premium and actual Google device/account tests; router details become necessary if direct networking is selected. F2.3 needs backend preference and first-release controls. Windows 11, Android and Google's cottage infrastructure are accepted. Outage behavior remains to be defined.

## Working rules

Workflow rules are in [AGENTS.md](../AGENTS.md) and card rules in [tasks](tasks/README.md). The shared dashboard reads these files as they are.
