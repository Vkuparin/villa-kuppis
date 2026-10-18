# First integration experiments

Experiment specifications, not results. Keep private records (account IDs, addresses, pairing codes) in ignored `scratch/`; publish only generic device labels and capability facts. Each live test needs the owner's go-ahead.

Order: C and D now; A and B when the cottage devices arrive.

## C. Mitsubishi at home

Follow [the Mitsubishi experiment](mitsubishi.md#first-experiment). Runs on the home PC.

## D. Spot prices and rules

No hardware needed. Fetch tomorrow's 15-minute FI prices from ENTSO-E (Elering as backup), store them, and compute plans from owner rules for the cottage heaters (heating) and one pump (cooling). Test against saved price fixtures with the network mocked.

## A. Cottage through Google Home

Prerequisite: Mill heaters and ALPSTUGA sensors commissioned into Google Home over Matter.

1. **Manual check, no code.** In the Google Home app and on [home.google.com](https://home.google.com), confirm all four heaters and both sensors appear. Set one heater to 5, 12 and 22 °C, and confirm each is accepted.
2. **Android app.** Build [Google's sample](https://github.com/google-home/google-home-api-sample-app-android) at a pinned revision with the `home.platform.selected.devices` scope. List the cottage devices and their traits; read an ALPSTUGA temperature and a heater's temperature, setpoint and mode.
3. **Remote.** Repeat over mobile data, away from the cottage.
4. **Setpoint.** Change one heater's setpoint, confirm the read-back, restore it.

Pass: steps 2–4 work over mobile data.

## B. Cottage price plan through the Automation API

1. From the app, create one automation with a clock-time starter that sets a heater's heating setpoint. Confirm it runs with the phone off.
2. Create a day's worth of automations from a sample plan. Record any limit on how many a home can hold.
3. Replace them from a background job, as the daily upload will.
4. Disable and re-enable them, as visit mode will.

Pass: a day's plan runs from Google Home with the phone and PC off. If it fails, see the alternatives in [platform decision](platform-decision.md#alternatives).

## Next step

| Evidence | Next |
| --- | --- |
| C and D pass | Build the PC service and the home side of the UI |
| A and B pass | Build the Android app's cottage view, daily upload and visit button |
| B fails | Owner decides between Mill cloud and the direct Matter route |
