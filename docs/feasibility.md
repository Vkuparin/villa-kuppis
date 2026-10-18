# Feasibility

## Assessment

Feasible. A Finnish UI and a PC service are ordinary work. The real unknowns are three integration questions, all testable cheaply:

1. Can the PC control the pumps locally? Likely yes, through the existing Wi-Fi adapter or a CN105 board. See [Mitsubishi](mitsubishi.md).
2. Can the PC control the cottage heaters? Yes through Home MCP during the Premium trial, if the trial includes it. After that, it depends on the Automation API fallback. See [platform decision](platform-decision.md).
3. Does Google expose the setpoints the plan needs (5 °C and 22 °C) for the Mill heaters over Matter?

Everything else (UI, history, spot prices, scheduling) depends only on our own code.

## Approach

A bespoke UI and a small service that reuses existing protocol libraries. Home Assistant, VMs and extra boxes are a last resort, used only if a device can be reached no other way. Do not reimplement Matter, Thread or Mitsubishi serial protocols.

## Reverse Engineer Anything (REA)

[REA](https://github.com/morluto/rea) lets a coding agent analyze apps and binaries through existing decompilers. Install it only from that repository or the `rea-agents` npm package: a fake copy has been distributed as a trojan. Pin the version used.

Where it helps here:

- The Mitsubishi Wi-Fi adapter or MELCloud app, if the adapter has a local endpoint that existing libraries do not cover.
- Any future device with no documented local API.

Where it does not help:

- Matter devices. Matter is an open standard with open-source controllers. Its sessions are encrypted with fabric keys, so captured traffic cannot be reused. The obstacle to reaching them is the network, not the protocol; see [cottage](cottage.md#secondary-route-direct-matter-from-the-home-pc).
- A device that only trusts its manufacturer's cloud. Reading the app does not make the device trust another server.

Ask one concrete question per REA session, such as "does this adapter accept commands on the LAN?", and stop after one or two sessions if the answer stays unclear. Keep app packages, captures and tokens in `scratch/`; publish only our own notes.

## Effort

The first useful result is one pump controlled locally, plus a price plan for it. Hardware tests dominate the schedule, not code.
