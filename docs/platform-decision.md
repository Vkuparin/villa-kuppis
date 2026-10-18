# Client and device access decision

Status: proposed, 18 October 2026, revision 5.

## Decided

- **Pumps:** the PC controls them through the MELCloud Home cloud.
- **Cottage:** Mill heaters and ALPSTUGA sensors in Google Home over Matter. The owner wants the heaters to follow the ALPSTUGA readings, and Matter keeps both in one place. Mill cloud mode was considered and rejected for this reason; the heaters run either Mill cloud or Matter, not both.
- **Clients:** a desktop web app served by the PC, and a native Kotlin Android app that shows the same web app and adds the cottage functions.
- **No Premium.** The speaker includes Premium Standard; Home MCP needs Advanced and is US-only.

## How the cottage is controlled

The PC cannot reach Google Home; only an Android or iOS app signed in as the user can. So the work is split:

| Job | Where |
| --- | --- |
| Prices, forecast, rules, optimizer, plans, history | PC |
| Reading the cottage | Android app, through the [Home APIs](https://developers.home.google.com/apis/android/connectivity); reported to the PC |
| Applying the plan | Android app uploads Google Home automations; Google runs them |
| Manual control | Villa Kuppis Android app, or the Google Home app and [home.google.com](https://home.google.com) |

## Sensor-driven automations

For each room and each plan period, the app uploads a pair of [Automation API](https://developers.home.google.com/apis/android/device/thermostat) automations:

- **When the room's ALPSTUGA reads below the period's target minus the hysteresis:** set that room's heaters to target + margin.
- **When it reads above the target plus the hysteresis:** set them to target − margin.

Defaults: margin 2 °C, hysteresis 0.3 °C; both are settings. The hysteresis stops the heaters flipping on every small change in the reading. Setpoints are clamped to each heater's reported range, so a 5 °C target's "off" setting becomes the heater's minimum rather than 3 °C. The heaters' own thermostats cap any overshoot at about the margin, even if a sensor stops reporting. If the heaters' own sensors read much warmer than the room, a larger margin may be needed; the graphs will show it.

Automations repeat daily. If the PC is down at 14:25 or an upload is missed, yesterday's plan runs again until a new one arrives.

**Fallback if sensor-triggered automations prove impossible or too limited:** upload time-based setpoints instead, each corrected by a per-room offset (the difference between the heaters' own readings and the ALPSTUGA), recalculated daily from history.

The plan is uploaded once a day, after the PC's daily price fetch at 14:25 and the new plan. A visit uploads at once. A **re-send** button covers a failed upload. If an upload is missed, the previous automations keep running.

The daily upload runs on one designated phone, the owner's, so household phones do not overwrite each other; any household phone can start or end a visit. It works wherever the phone has internet. The weak points are Android delaying background jobs in battery saver, and no data connection at that hour. The app notifies on a failed upload, and the web app shows a warning if the PC has had no upload confirmation by 15:30.

**Unverified:** whether the Home APIs work from an Android background job at all. If they only work while the app is open, the daily upload becomes a notification to tap, and cottage readings are recorded only while the app runs.

**To verify when the devices arrive:**

- that an automation can start on an ALPSTUGA temperature change and set the heaters;
- that time-of-day conditions can limit an automation to its plan period;
- how many automations a home can hold;
- that a background job can replace them, and can read devices at all.

If the automation count is limited, merge plan periods with the same target.

## Setup notes

Start from [Google's Android sample app](https://github.com/google-home/google-home-api-sample-app-android). Its [authorization guide](https://developers.home.google.com/apis/android/sample-app/authorization) defaults to the partner-gated `home.platform.v2` scope; use `HOME_PLATFORM_SCOPE_VERSION_1` (`home.platform.selected.devices`). Add household accounts as OAuth test users; [unregistered apps allow 100 users](https://developers.home.google.com/apis/android/oauth). A Google Cloud project and OAuth credentials are needed. No usage fee was found.

## Not pursued

- **Mill cloud mode:** the heaters could not follow the ALPSTUGA sensors.
- **Direct Matter from the PC:** needs routed IPv6 into the cottage network, which the current routers are unlikely to provide; see [cottage](cottage.md).
- **Home MCP:** Premium Advanced and US-only.
