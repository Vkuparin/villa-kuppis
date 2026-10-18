# First integration experiments

Experiment specifications, not results. Keep private records (credentials, account and device IDs) in ignored `scratch/`; publish only generic device labels and capability facts. Each live test needs the owner's go-ahead.

Order: C and D now; A and B when the cottage devices arrive.

## C. Mitsubishi through MELCloud Home

Follow [the Mitsubishi experiment](mitsubishi.md#first-experiment). Runs on the home PC.

## D. Spot prices and fixed thresholds

No hardware needed. Fetch tomorrow's 15-minute FI spot prices from ENTSO-E (Elering as backup), store them, and compute plans from fixed thresholds per room and per site, for heating and cooling. Test against saved price fixtures with the network mocked.

## A. Cottage through the Home APIs

Prerequisite: heaters and sensors commissioned into Google Home over Matter.

1. Build [Google's sample](https://github.com/google-home/google-home-api-sample-app-android) at a pinned revision with the `home.platform.selected.devices` scope.
2. List the cottage devices and their traits. Read each ALPSTUGA (temperature, CO2, PM2.5, humidity, as exposed) and each heater (temperature, setpoint, mode).
3. Over mobile data, change one heater's setpoint, confirm the read-back, restore it.
4. Run a background job that reads all devices every 15 minutes for a day; record how often Android actually runs it.

Pass: steps 2–3 work over mobile data. Step 4 shows how complete the cottage graphs will be.

## B. Sensor-driven automations

1. From the app, create the automation pair for the small room: below target, set the heater to target + 2 °C; above, set it to target − 2 °C.
2. Confirm it reacts to the ALPSTUGA reading with the phone off.
3. Add a time-of-day condition so the pair only applies in one plan period.
4. Upload a full day's plan for both rooms. Record any limit on the number of automations.
5. Replace the plan from a background job, as the daily upload will.

Pass: a day's plan runs from Google Home with the phone and PC off. If it fails, see [platform decision](platform-decision.md#sensor-driven-automations).

## Next step

| Evidence | Next |
| --- | --- |
| C and D pass | Build the PC service and the home side of the web app |
| A and B pass | Build the Android app's cottage functions and the daily upload |
| B fails | Rethink cottage control before building the upload |
