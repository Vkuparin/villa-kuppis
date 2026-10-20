# Feasibility

## Assessment

Feasible. The PC service, web app, prices and fixed thresholds are ordinary work. The main unknowns, all testable cheaply:

1. Does `aiomelcloudhome` read and set the three pumps? Yes: live checks passed on 19 October 2026. See [Mitsubishi](mitsubishi.md).
2. Does Home Assistant's MELCloud Home integration match today's client closely enough to keep the cooling rules (H1.3)? If not, the direct client stays for the pumps.
3. Do the Mill Gen 4 heaters and ALPSTUGA sensors expose what the cottage needs over Matter on Home Assistant: a 5 °C setpoint, reliable read-back, the sensor readings (H2.1)?
4. Does the Google speaker work as the Thread border router for Home Assistant, including with the cottage's internet down (H2.1)? If not, a supported local Thread border router is needed.
5. Is a partly work-managed phone allowed to run Tailscale? If not, it needs a separate authenticated route later.

The optimizer is the biggest piece of our own code. It needs a few weeks of readings before it can learn each room's heat-up and cool-down rates.

## Approach

A bespoke Finnish web app and Python planning service, with one Home Assistant per site as the device gateway and local fallback (decided 20 October 2026). Take the simplest working route to each device, even through the manufacturer's cloud.

## Reverse Engineer Anything (REA)

[REA](https://github.com/morluto/rea) lets a coding agent analyze apps and binaries through existing decompilers. Install it only from that repository or the `rea-agents` npm package: a fake copy has been distributed as a trojan. Pin the version used.

**Where it helps:** the MELCloud Home client is unofficial, reverse engineered by the community from Mitsubishi's app. If MELCloud Home changes its API and the library breaks, REA on the current app shows the new requests. Ask one concrete question per session, such as "which request sets the target temperature?", and stop after one or two sessions if the answer stays unclear. Keep app packages, captures and tokens in `scratch/`; publish only our own notes.

**Where it does not help:** Matter devices, whose protocol is already open and whose sessions are encrypted with keys only the controlling app holds.

## Effort

First useful result: one pump controlled from the PC, and tomorrow's price plan computed from fixed thresholds. Hardware tests dominate the schedule, not code.
