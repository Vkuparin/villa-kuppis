# Feasibility

## Assessment

Feasible. A Finnish web app, a PC service and a spot-price planner are ordinary work. Two integration questions remain, both cheap to test:

1. Does `aiomelcloudhome` read and set the three pumps through MELCloud Home? See [Mitsubishi](mitsubishi.md).
2. Does `millheater` read and set a Gen 4 heater in Mill cloud mode, including 5, 12 and 22 °C? See [platform decision](platform-decision.md).

Everything else (UI, history, prices, rules) depends only on our own code.

## Approach

A bespoke web app and a small Python service that reuse existing client libraries. Take the simplest working route to each device, even through the manufacturer's cloud. Home Assistant, VMs and extra boxes are a last resort.

## Reverse Engineer Anything (REA)

[REA](https://github.com/morluto/rea) lets a coding agent analyze apps and binaries through existing decompilers. Install it only from that repository or the `rea-agents` npm package: a fake copy has been distributed as a trojan. Pin the version used.

Where it helps here: both cloud clients are unofficial, reverse engineered by the community from the manufacturers' apps. If MELCloud Home or Mill changes its API and a library breaks, or a needed function is missing, REA on the current app shows the new requests. Ask one concrete question per session, such as "which request sets the target temperature?", and stop after one or two sessions if the answer stays unclear. Keep app packages, captures and tokens in `scratch/`; publish only our own notes.

Where it does not help: Matter devices, whose protocol is already open and whose sessions are encrypted with keys only the controlling app holds.

## Effort

The first useful result is one pump controlled from the PC and tomorrow's price plan computed from owner thresholds. Hardware tests dominate the schedule, not code.
