# First integration experiments

Experiment specifications and recorded checks. Keep private reference material in ignored `scratch/`; publish only generic device labels and capability facts. The pump probe reads credentials through masked local prompts and does not save them. Each live test needs the owner's go-ahead.

Order: C and D first, when implementation begins; A and B when the cottage devices are installed.

## C. Mitsubishi through MELCloud Home

Follow [the Mitsubishi experiment](mitsubishi.md#first-experiment). Runs on the home PC.

The supervised probe uses `aiomelcloudhome` 0.3.1. Its offline demo changes a synthetic unit from 23 °C to 24 °C and restores 23 °C.

Validated on 19 October 2026 at `9f627d7`: 17 focused pump tests passed on Windows Python 3.13, with network, credentials and devices replaced by fakes. The offline demo and help passed. Tests cover secure-prompt failure, bounds and confirmation checks, failed writes and read-back, restoration failure, interruption, other controls changing, and sanitized errors. Sol 6.1 Medium reviewed the design and implementation; Kynsi implemented the probe and one repair pass.

Owner-approved live check on 19 October 2026: sign-in and cloud state reading succeeded for the upstairs LN25. It reported Cool mode, power off and a 16–31 °C setpoint range. The write test returned exit code 3 without confirming the change or restoration. The owner then confirmed the original setpoint, mode and power in MELCloud Home. The console error detail was unavailable, so the cause remains unresolved. Investigate response handling and restoration verification offline; do not assume the unit must be switched on.

## D. Spot prices and fixed thresholds

No hardware needed. Fetch tomorrow's 15-minute FI spot prices from Elering (no key), store them, and compute plans from fixed thresholds per room and per site, for heating and cooling. Test against saved price fixtures with the network mocked.

The Python experiment lives in `villa_kuppis/`; [README](../README.md#development) has the offline demo and test commands. Its saved fixtures are synthetic Elering-format responses for a normal day and both 2026 daylight-saving changes. They are test data, not historical market prices or evidence of a live fetch.

The [Elering API specification](https://dashboard.elering.ee/v3/api-docs) defines `start` and `end` as date-time query strings. Returned interval timestamps are Unix seconds. The experiment stores raw €/MWh prices, converts them to c/kWh for planning, and rejects incomplete days before replacing saved prices.

This step computes temperature targets. Cooling shutoff, measured-temperature checks, minimum off time and device commands remain in [F4.1](tasks/0.2.0.md#f6-pump-automation).

Validated on 19 October 2026 at `29cd774`: 102 tests passed in the Windows Python 3.13 project environment. The offline demo ran in a fresh temporary data directory, and help, document parsing and whitespace checks passed. Tests cover both DST days, failed fetches and database writes preserving saved prices, room overrides, strict thresholds, daily scheduling across restarts, and CLI validation before network access. Network boundaries were mocked in these tests.

Owner-approved live check on 19 October 2026, using the implementation at `62dedd0`: one Elering fetch stored all 96 FI intervals for that Helsinki day in a new temporary SQLite database. A separate database read confirmed 00:00 through 23:45 at 900-second spacing without another fetch. This validates on-demand retrieval and persistence; the unattended daily scheduler has only mocked test coverage.

## A. Cottage through the Home APIs

Prerequisite: heaters and sensors commissioned into Google Home over Matter.

1. Build [Google's sample](https://github.com/google-home/google-home-api-sample-app-android) at a pinned revision with the `home.platform.selected.devices` scope.
2. List the cottage devices and their traits. Read each ALPSTUGA (temperature, CO2, PM2.5, humidity, as exposed) and each heater (temperature, setpoint, mode).
3. Over mobile data, change one heater's setpoint, confirm the read-back, restore it.
4. Run a background job that reads all devices every 15 minutes for a day. Record whether the Home APIs work in the background at all, and how often Android actually runs the job.
5. Check whether the heaters expose power or energy over Matter.

Pass: steps 2–3 work over mobile data. Step 4 shows how complete the cottage graphs will be.

## B. Sensor-driven automations

1. Give the cottage home a street address in Google Home (needed for time-based starters).
2. From the app, create the small room's automation: every 30 minutes, read the ALPSTUGA; below target minus hysteresis, set the heater to target + margin; above target plus hysteresis, set it to target − margin.
3. Confirm it sets the heater's absolute setpoint and runs with the phone off.
4. Encode a day of 12 time windows with different targets in both rooms' automations. Confirm it fits Google's limits and follows the windows.
5. Replace both automations from a background job, as the daily upload will.
6. Try one automation with separate branches for both rooms, every 15 minutes.

Pass: a day's plan runs from Google Home with the phone and PC off. If it fails, use the time-based fallback in [platform decision](platform-decision.md#sensor-driven-automations).

## Next step

| Evidence | Next |
| --- | --- |
| C and D pass | Build the PC service and the home side of the web app |
| A and B pass | Build the Android app's cottage functions and the daily upload |
| B fails | Use the time-based fallback in [platform decision](platform-decision.md#sensor-driven-automations) |
