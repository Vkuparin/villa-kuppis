# Client and device access decision

Status: proposed, 18 October 2026, revision 3.

## Decided

- The PC talks to every controllable device through its manufacturer's cloud: MELCloud Home for the pumps, Mill cloud for the heaters. Everything, including price automation, runs on the PC.
- One web app served by the PC, used from a desktop browser and installed on Android phones over Tailscale.
- Premium is not used, and nothing in the design needs Google's developer APIs.

## Why Mill cloud rather than Matter

The Mill heaters run either Mill cloud or Matter, not both.

| | Mill cloud | Matter through Google Home |
| --- | --- | --- |
| PC controls the heaters | Yes, directly | No. Google Home MCP needs Premium Advanced and is US-only; the speaker includes Standard. |
| Price automation | PC changes setpoints every 15 minutes, same engine as the pumps | Android app uploads a daily plan as Google Home automations; limits untested |
| Desktop control | In Villa Kuppis | Only through home.google.com |
| Needs | `millheater` working with Gen 4; Mill cloud up | Native Kotlin Android app with the Home APIs and a daily background job |
| Google Home app and voice | Heaters not in Google Home (no official Mill integration found) | Heaters in Google Home |

Mill cloud is simpler. It removes the native Android app, the Google APIs and the daily upload.

**Check first:** set up the first heater in Mill cloud mode and confirm `millheater` reads and sets it from the PC. Gen 4 support in the library is unverified. If it fails, switch the heaters to Matter and use the Google route below.

## Fallback: Matter through Google

Keep this only if Mill cloud fails:

- Commission the heaters into Google Home over Matter.
- Build a small Kotlin Android app with the [Home APIs](https://developers.home.google.com/apis/android/connectivity) (scope `home.platform.selected.devices`).
- The app fetches the PC's daily plan and writes it as Google Home automations ([Automation API](https://developers.home.google.com/apis/android/device/thermostat), clock-time starters).

## ALPSTUGA

The sensors are Matter over Thread and only reach Google Home, through the speaker. They stay in the Google Home app. The Mill heaters report their own room temperature, which is enough for the plan and the UI. Bringing ALPSTUGA readings into Villa Kuppis later needs a native Android app with the Home APIs.
