# First integration experiments

Experiment specifications, not results. Keep private records (credentials, account and device IDs) in ignored `scratch/`; publish only generic device labels and capability facts. Each live test needs the owner's go-ahead.

Order: C and D now; A when the heaters arrive.

## C. Mitsubishi through MELCloud Home

Follow [the Mitsubishi experiment](mitsubishi.md#first-experiment). Runs on the home PC.

## D. Spot prices and rules

No hardware needed. Fetch tomorrow's 15-minute FI spot prices from ENTSO-E (Elering as backup), store them, and compute plans from owner thresholds for the cottage heaters (heating) and one pump (cooling). Test against saved price fixtures with the network mocked.

## A. Mill heater through Mill cloud

Prerequisite: one heater set up in Mill cloud mode in the Mill app.

1. From the PC, sign in with `millheater` and list the heater.
2. Read its room temperature, setpoint and mode; compare with the Mill app.
3. Set 5, 12 and 22 °C in turn, confirming each read-back, then restore the original.

Pass: steps 1–3 work. If the library does not support Gen 4, try the Mill app's current API with REA before falling back to Matter ([platform decision](platform-decision.md#fallback-matter-through-google)).

## Next step

| Evidence | Next |
| --- | --- |
| C and D pass | Build the PC service and the home side of the web app |
| A passes | Set up all four heaters in Mill cloud mode; add the cottage to the app and the price plans |
| A fails | Owner decides between repairing the cloud client and the Matter fallback |
