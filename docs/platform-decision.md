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

Google's documented [automation limits](https://developers.home.google.com/apis/android/automation) shape the design: 64 automations per home, 128 runs per day per developer per home, and 64 comparisons per automation. Automations triggered by every sensor change would exceed the run limit, so the heaters follow the sensors on a timer instead.

**Uploaded once a day, run every 30 minutes.** The phone uploads the day's automations once, after the 14:25 price fetch. The plan inside them (which target applies when) is fixed for the day. Google then runs them every 30 minutes on its own, to compare each room's ALPSTUGA with the plan and nudge the heaters. Nothing is sent from the phone or PC between uploads. The repeated runs are needed because Google cannot make a heater use the ALPSTUGA instead of its own thermostat. The owner chose this over changing setpoints only at plan times, which would be less precise.


- **One automation per room** (two in total), run by Google every 30 minutes through a recurring clock-time starter: 2 × 48 = 96 runs a day, under the 128 limit.
- **Each run** reads the room's ALPSTUGA and the plan target for the current time window, then sets that room's heaters. If the reading is below target minus the hysteresis, the heaters go to target + margin. If it is above target plus the hysteresis, they go to target − margin.
- **The day's plan is encoded as time-window conditions** inside each automation. To fit the comparison limit, the planner merges each room's day into at most 12 time windows (a setting).
- **Defaults:** margin 2 °C, hysteresis 0.3 °C; both are settings. Setpoints are clamped to each heater's reported range, so a 5 °C target's "off" setting becomes the heater's minimum rather than 3 °C. Between runs the heaters' own thermostats cap any overshoot at about the margin, even if a sensor stops reporting. If the heaters' own sensors read much warmer than the room, a larger margin may be needed; the graphs will show it.
- **Finer control:** if one automation can hold separate branches per room, a single cottage automation every 15 minutes (96 runs a day) is the alternative.

The automations repeat daily. If the PC is down at 14:25 or an upload is missed, yesterday's windows run again until a new plan arrives. Google's [overview](https://developers.home.google.com/apis/android/overview) says its automation engine stores and runs automations, so they keep running with the phone and PC off. Time-based starters need a street address on the cottage home in Google Home.

The plan is uploaded once a day, after the PC's daily price fetch at 14:25 and the new plan. A visit uploads at once. A **re-send** button covers a failed upload.

The daily upload runs on one designated phone, the owner's, so household phones do not overwrite each other; any household phone can start or end a visit. It works wherever the phone has internet. The app notifies on a failed upload, and the web app shows a warning if the PC has had no upload confirmation by 15:30.

**Fallback** if setting a heater's setpoint from an automation or reading the ALPSTUGA in a condition is not possible: upload time-based setpoints instead, each corrected by a per-room offset (the gap between the heaters' own readings and the ALPSTUGA), recalculated daily from history.

**Documented, 18 October 2026:**

- Automations can start at clock times or on a recurring schedule, at most once a minute.
- Conditions can compare any device attribute with `lessThan`, `greaterThan` and similar operators, and can compare the current time.
- Actions can be commands or state updates.
- An app can hold permission for only one Google Home home at a time; Villa Kuppis needs only the cottage.
- Only OAuth test users (at most 100) can grant permissions.
- The Home APIs need a physical Android 10+ phone and a hub; the rate limit is 30,000 queries a minute per project; no price is listed.

**Not documented; test when the devices arrive:**

- whether an automation can set a Mill heater's absolute heating setpoint;
- whether the ALPSTUGA temperature can be read in a condition;
- whether the Home APIs work from an Android background job (if not, the daily upload becomes a notification to tap, and cottage readings are recorded only while the app runs);
- whether permissions of an unpublished test app expire after 7 days.

## Setup notes

Start from [Google's Android sample app](https://github.com/google-home/google-home-api-sample-app-android). Its [authorization guide](https://developers.home.google.com/apis/android/sample-app/authorization) defaults to the partner-gated `home.platform.v2` scope; use `HOME_PLATFORM_SCOPE_VERSION_1` (`home.platform.selected.devices`). Add household accounts as OAuth test users; [unregistered apps allow 100 users](https://developers.home.google.com/apis/android/oauth). A Google Cloud project and OAuth credentials are needed. No usage fee was found.

## Not pursued

- **Mill cloud mode:** the heaters could not follow the ALPSTUGA sensors.
- **Direct Matter from the PC:** needs routed IPv6 into the cottage network, which the current routers are unlikely to provide; see [cottage](cottage.md).
- **Home MCP:** Premium Advanced and US-only.
