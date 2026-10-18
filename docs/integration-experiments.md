# First integration experiments

Experiment specifications, not results. Keep private records (account IDs, addresses, pairing codes) in ignored `scratch/`; publish only generic device labels and capability facts. Each live test needs the owner's go-ahead.

## A. Cottage through Google Home

Prerequisite: Mill heaters and ALPSTUGA sensors commissioned into Google Home over Matter.

1. **Manual check, no code.** In the Google Home app and on [home.google.com](https://home.google.com), confirm all four heaters and both sensors appear. Set one heater to 5 °C and to 22 °C, and confirm both values are accepted. Note the minimum and maximum each interface offers.
2. **Android app.** Build [Google's sample](https://github.com/google-home/google-home-api-sample-app-android) at a pinned revision with the `home.platform.selected.devices` scope. List the cottage devices and their traits; read an ALPSTUGA temperature and a heater's temperature, setpoint and mode.
3. **Remote.** Repeat over mobile data, away from the cottage, with the home PC off.
4. **Setpoint.** Change one heater's setpoint, confirm the read-back, restore it.
5. **Automation API.** From the app, create one time-started automation that sets a heater setpoint. Confirm it runs with the phone off, then delete it.

Pass: steps 2–4 work over mobile data. Step 5 decides whether the no-Premium fallback for spot-price automation is viable.

## B. Cottage from the PC through Home MCP

Prerequisite: the Premium trial is active and includes Advanced and MCP access. Do not buy anything to make it pass.

1. Authenticate from the home PC; list the cottage devices.
2. Read a sensor and a heater, including timestamps and connectivity.
3. Change one heater's setpoint, confirm through a state read, restore it. Deterministic code chooses the target and value, never a model.
4. Restart the PC service and confirm it reconnects without a phone.

Pass: the PC reads and sets cottage heaters on its own. This makes cottage spot-price automation possible during the trial.

## C. Mitsubishi at home

Follow [the Mitsubishi experiment](mitsubishi.md#first-experiment). Independent of Google and the cottage.

## D. Spot prices

No hardware needed. Fetch tomorrow's 15-minute FI prices from the chosen source, store them, and compute a plan for one pump and the cottage band. Test against saved price fixtures.

## Next step after A–D

| Evidence | Next |
| --- | --- |
| C and D pass | Build the home side: pump control and price automation |
| B passes | Add cottage automation through MCP |
| A5 passes | Build the post-trial fallback in the Android app |
| A5 fails, B passes | Cottage price automation lasts only as long as Premium; decide on renewal, a fixed schedule or the direct Matter route |
