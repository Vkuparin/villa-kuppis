# Feasibility

## Assessment

Feasible. A Finnish UI, a PC service and a spot-price planner are ordinary work. The real unknowns are three integration questions, all cheap to test:

1. Can the PC control the pumps locally? Likely yes, through the existing Wi-Fi adapter with `pymitsubishi`, or a CN105 board. See [Mitsubishi](mitsubishi.md).
2. Can the Android app upload a day's cottage plan as Google Home automations? The PC cannot reach Google Home from Finland, so this is the cottage automation route. See [platform decision](platform-decision.md).
3. Does Google accept the setpoints the rules need (5–22 °C) for the Mill heaters over Matter?

Everything else (UI, history, prices, rules) depends only on our own code.

## Approach

A bespoke UI and a small Python service that reuses existing protocol libraries. Home Assistant, VMs and extra boxes are a last resort, used only if a device can be reached no other way. Do not reimplement Matter, Thread or Mitsubishi serial protocols.

## Reverse Engineer Anything (REA)

[REA](https://github.com/morluto/rea) lets a coding agent analyze apps and binaries through existing decompilers. Install it only from that repository or the `rea-agents` npm package: a fake copy has been distributed as a trojan. Pin the version used.

Where it helps here:

- The Mitsubishi Wi-Fi adapter or MELCloud app, if the adapter answers locally but `pymitsubishi` lacks a needed function.
- Any future device with no documented local API.

Where it does not help:

- Matter devices. Matter is an open standard with open-source controllers, and its sessions are encrypted with fabric keys, so captured traffic cannot be reused. The obstacle is the network, not the protocol; see [cottage](cottage.md#secondary-route-direct-matter-from-the-home-pc).
- A device that only trusts its manufacturer's cloud.

Ask one concrete question per REA session, such as "does this adapter accept commands on the LAN?", and stop after one or two sessions if the answer stays unclear. Keep app packages, captures and tokens in `scratch/`; publish only our own notes.

## Effort

The first useful result is one pump controlled locally from the PC, and tomorrow's price plan computed from owner rules. Hardware tests dominate the schedule, not code.
