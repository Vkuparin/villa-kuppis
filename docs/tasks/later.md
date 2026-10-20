# Later: release not assigned

Ideas the owner agreed to that have no release yet. Add a feature heading and the standard card table when there is concrete work; keep IDs when a card moves to a release. Card rules are in [README.md](README.md).

## W1. Agent workflow

| ID | Task and Done when | Agent | Depends on | Status |
| --- | --- | --- | --- | --- |
| W1.1 | Let Kynsi coordinate and implement T2–T3, use Sol 6.1 Light for completed T2 batch reviews and Medium for T1, and allow Codex subscription agents from Hermes while preserving manual Claude coordination. Done when project, template and bridge checks pass. | Tier 1 | | Done: workflow 02df30a and approved repair 7a8987e; template 82fc5c3/d989896; bridge 67753c9/d01b459. Twenty-one Node bridge checks, four Python installer checks, actual Codex plugin parser, nineteen template checks, document and whitespace checks passed. Actual Kynsi-to-Codex subscription review and re-review completed; re-review had no findings. Plugin 0.4.1 installed, Hermes policy updated and Hermes-only Claude provider disabled. Manual Claude workflows and owner settings preserved. |

## L1. Beyond the first releases

| ID | Task and Done when | Agent | Depends on | Status |
| --- | --- | --- | --- | --- |
| L1.1 | No-Premium fallback for cottage price plans through the Android app. | Tier 1 | F2.2 | Dropped: now the primary route, F4.2 |
| L1.2 | Secondary route: direct Matter control of cottage devices from the PC. | Tier 1 | F2.2 | Dropped: current cottage gear unlikely to route IPv6 |
| L1.3 | Start visit mode from a Google Calendar event. | Tier 2 | F4.3 | Dropped: owner prefers a button |
| L1.4 | Show measured cottage heat-up and cool-down rates. | Tier 2 | F4.2 | Dropped: part of F5.1 |
| L1.5 | "Cheapest hours" rule. | Tier 2 | F4.2 | Dropped: superseded by the optimizer, F5.2 |
| L1.6 | Show ALPSTUGA readings in Villa Kuppis. | Tier 1 | F3.2 | Dropped: now core, F3.1 and F3.3 |

## H3. Two-site Home Assistant follow-ups

| ID | Task and Done when | Agent | Depends on | Status |
| --- | --- | --- | --- | --- |
| H3.3 | Evaluate local cached price schedules at the cottage (a whole day's plan executed by the cottage Home Assistant during outages). Not implemented automatically. Done when outage tests quantify the value and a scoped decision builds or defers a cottage runner. | Tier 1 | H2.4, H3.2 | Planned |
