# Client and cottage access decision

Status: proposed, 18 October 2026.

## Decided

- Android phones and a desktop browser. The PC service is the shared backend and the only place plans are computed.
- Google Home is the cottage hub. One Google account holds both homes; the home already has a Google Nest Wifi Pro.
- No Home Assistant box, VM or cottage computer unless every other route fails.

## What the PC cannot do

The PC has no supported way to talk to Google Home from Finland today:

- The Home APIs exist only for Android and iOS.
- [Google Home MCP](https://developers.home.google.com/mcp/home) requires Premium **Advanced** and, during early access, a US account in English ([coverage](https://dev.to/techaiwire/google-home-adds-an-mcp-server-gated-at-20-a-month-5gop)). The Google Home Speaker comes with six months of Premium **Standard** ([offer](https://chromeunboxed.com/gemini-live-might-be-the-real-reason-to-buy-the-new-google-home-speaker-and-you-can-try-it-free-for-6-months/)). Premium is therefore not part of the design.

Being at home does not change this: linking an app to Google Home is an OAuth sign-in, not a local pairing.

## How the cottage is controlled

| Use | Route |
| --- | --- |
| Manual control | Google Home app, [home.google.com](https://home.google.com) on desktop, or the Villa Kuppis Android app |
| Price automation | The Android app uploads the PC's daily plan as Google Home automations ([Automation API](https://developers.home.google.com/apis/android/device/thermostat)); Google runs them |
| Visit button | The Android app sets the heaters directly and suspends the automations |
| Desktop UI | Shows the cottage plan and the state the Android app last reported; manual cottage control from the desktop goes through home.google.com |

The Automation API supports thermostat elements and clock-time starters. Still to verify: that an automation can set a heater's heating setpoint, how many automations a home can hold, and that a background job can replace them daily.

The upload needs an Android phone that runs a daily background job. A spare Android phone left on a charger at home is more dependable than a personal phone that travels.

## Alternatives

| Route | Gain | Cost | Status |
| --- | --- | --- | --- |
| Mill cloud instead of Matter for the heaters | PC controls the heaters directly every 15 minutes; no phone needed | Mill cloud dependency; Gen 4 cloud API unverified; the manuals offer cloud **or** Matter | Owner decision if the Automation API falls short |
| Direct Matter from the PC | No Google or phone in the automation path | Routed IPv6 to the cottage; probably router changes | Secondary research; see [cottage](cottage.md#secondary-route-direct-matter-from-the-home-pc) |
| Premium Advanced, if MCP reaches Finland | PC controls the cottage through Google | About $20 a month | Watch; not planned |

## Client choice

A native Kotlin Android app, because the Home APIs and the background upload need it. The desktop UI is a browser app served by the PC.
