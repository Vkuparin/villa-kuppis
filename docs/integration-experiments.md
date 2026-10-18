# First integration experiments

Experiment specifications, not results. Keep private records (credentials, account and device IDs) in ignored `scratch/`; publish only generic device labels and capability facts. Each live test needs the owner's go-ahead.

Order: C and D first, when implementation begins; A and B when the cottage devices are installed.

## C. Mitsubishi through MELCloud Home

Follow [the Mitsubishi experiment](mitsubishi.md#first-experiment). Runs on the home PC.

## D. Spot prices and fixed thresholds

No hardware needed. Fetch tomorrow's 15-minute FI spot prices from Elering (no key), store them, and compute plans from fixed thresholds per room and per site, for heating and cooling. Test against saved price fixtures with the network mocked.

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
