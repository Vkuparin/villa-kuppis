# Feasibility

## Assessment

Feasible. The PC service, web app, prices and fixed thresholds are ordinary work. The main unknowns, all testable cheaply:

1. Does `aiomelcloudhome` read and set the three pumps? See [Mitsubishi](mitsubishi.md).
2. Can a Google Home automation read the ALPSTUGA and set a Mill heater's absolute setpoint, within Google's documented limits (64 automations, 128 runs a day)? See [platform decision](platform-decision.md#sensor-driven-automations).
3. Do the Home APIs work from an Android background job, and how often does Android run it? This decides whether the daily upload is automatic and how complete the cottage graphs and the optimizer's data will be.

The optimizer is the biggest piece of our own code. It needs a few weeks of readings before it can learn each room's heat-up and cool-down rates.

## Approach

A bespoke web app, a native Android app for Google Home, and a small Python service that reuses existing client libraries. Take the simplest working route to each device, even through the manufacturer's cloud. Home Assistant, VMs and extra boxes are a last resort.

## Reverse Engineer Anything (REA)

[REA](https://github.com/morluto/rea) lets a coding agent analyze apps and binaries through existing decompilers. Install it only from that repository or the `rea-agents` npm package: a fake copy has been distributed as a trojan. Pin the version used.

**Where it helps:** the MELCloud Home client is unofficial, reverse engineered by the community from Mitsubishi's app. If MELCloud Home changes its API and the library breaks, REA on the current app shows the new requests. Ask one concrete question per session, such as "which request sets the target temperature?", and stop after one or two sessions if the answer stays unclear. Keep app packages, captures and tokens in `scratch/`; publish only our own notes.

**If Google restricts the Home APIs route:** REA on the Google Home web app ([home.google.com](https://home.google.com)), which already controls thermostats, could show its private requests. That would be fragile, since it relies on signed-in sessions and unpublished APIs, and probably against Google's terms; a last resort.

**Where it does not help:** Matter devices, whose protocol is already open and whose sessions are encrypted with keys only the controlling app holds.

## Effort

First useful result: one pump controlled from the PC, and tomorrow's price plan computed from fixed thresholds. Hardware tests dominate the schedule, not code.
