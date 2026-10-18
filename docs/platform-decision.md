# Client and device access decision

Status: proposed, 18 October 2026, revision 4.

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

- **When the room's ALPSTUGA reads below the period's target:** set that room's heaters to target + 2 °C.
- **When it reads above the target:** set them to target − 2 °C.

The heaters' own thermostats cap any overshoot at about 2 °C, even if a sensor stops reporting.

The plan is uploaded once a day, after next-day prices are published around 14:00. A visit uploads at once. A **re-send** button covers a failed upload. If an upload is missed, the previous automations keep running.

The owner's own phone does the upload. It works wherever the phone has internet. The weak points are Android delaying background jobs in battery saver, and no data connection at that hour; the app sends a notification if the upload fails.

**To verify when the devices arrive:**

- that an automation can start on an ALPSTUGA temperature change and set the heaters;
- that time-of-day conditions can limit an automation to its plan period;
- how many automations a home can hold;
- that a background job can replace them.

If the automation count is limited, merge plan periods with the same target.

## Setup notes

Start from [Google's Android sample app](https://github.com/google-home/google-home-api-sample-app-android). Its [authorization guide](https://developers.home.google.com/apis/android/sample-app/authorization) defaults to the partner-gated `home.platform.v2` scope; use `HOME_PLATFORM_SCOPE_VERSION_1` (`home.platform.selected.devices`). Add household accounts as OAuth test users; [unregistered apps allow 100 users](https://developers.home.google.com/apis/android/oauth). A Google Cloud project and OAuth credentials are needed. No usage fee was found.

## Not pursued

- **Mill cloud mode:** the heaters could not follow the ALPSTUGA sensors.
- **Direct Matter from the PC:** needs routed IPv6 into the cottage network, which the current routers are unlikely to provide; see [cottage](cottage.md).
- **Home MCP:** Premium Advanced and US-only.
