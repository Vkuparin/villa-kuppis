# Client and cottage access decision

Status: proposed, 18 October 2026.

## Decided

- Android phones and a desktop browser. The PC service is the shared backend.
- Google Home is the cottage hub; its cloud dependency is accepted.
- Google Home Premium is available for six months from the speaker offer. Features may use it, but each needs a fallback in case the owner does not renew.
- No Home Assistant box, VM or cottage computer unless every other route fails.

## Manual control

Manual cottage control already works without any of our code: the Google Home app on Android and [home.google.com](https://home.google.com) on desktop. Villa Kuppis adds one Finnish view of both sites, with the pumps alongside the cottage.

## PC automation of the cottage

Spot-price automation needs the PC to change cottage setpoints.

| Route | How | Needs | Role |
| --- | --- | --- | --- |
| [Google Home MCP](https://developers.home.google.com/mcp/home) | PC calls Google's MCP HTTP endpoint directly, from deterministic code | Premium Advanced; early access | Primary during the trial |
| Automation API via the Android app | Once a day, after next-day prices publish, the app writes the cottage price plan into Google Home automations with time-based starters. Google runs them; the PC and phone can be off. | An Android phone that runs a daily background job | Fallback after the trial |
| Fixed schedule | Away band set once, visit mode switched by hand | Nothing | Last-resort fallback |
| Direct Matter | PC as its own Matter controller | Routed IPv6 to the cottage, probably a cottage router | Secondary research; see [cottage](cottage.md#secondary-route-direct-matter-from-the-home-pc) |

The Automation API fallback fits spot prices well: day-ahead prices are known a day in advance, so a once-a-day upload is enough. If the phone misses a day, the heaters keep their last setpoint, which is always inside the 5–8 °C band.

Points to verify:

- **MCP:** the trial includes Advanced and MCP early access, and the MCP actions cover Matter thermostat setpoints. The [action reference](https://developers.home.google.com/reference/home/mcp/tools_list/run_home_actions) has direct HTTP examples. Device IDs and parameters come from discovery, never from guessed names.
- **Automation API:** it can create time-started automations that set a thermostat setpoint, enough of them for a day's plan; and the app can update them from a background job.

## Client choice

Build the first experiment as a small Kotlin Android app using the standard Home APIs scope. Choose between a native Android app and a PWA once the Automation API fallback is tested: if the fallback works, the app needs to be native anyway.
