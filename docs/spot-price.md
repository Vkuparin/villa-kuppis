# Spot-price automation

High priority. The owner sets the price thresholds and the temperatures; the app applies them for every 15-minute price period. Both sites are on spot-price contracts with no day/night transfer tariff.

## Price data

- Finnish day-ahead prices (bidding zone FI) in 15-minute periods, the Nord Pool market unit since 1 October 2025. Next-day prices are usually published around 14:00 Finnish time.
- Primary source: [ENTSO-E Transparency Platform](https://transparency.entsoe.eu/) (free API token). Backup: Elering's public API, which needs no key and returns 15-minute points. Cache prices in SQLite.
- Prices are shown and compared in c/kWh. Whether thresholds include VAT is a setting; see [questions](questions.md).

## Rules

Each site, or group of devices, has a **base** setpoint and a list of owner-defined rules. Two rule types:

- **Price threshold:** "when the price is below X c/kWh, use T". Example: below 5 c/kWh, heat the cottage to 12 °C.
- **Cheapest periods (optional):** "in the cheapest N quarter-hours of the day, use T". Useful in winter, when prices may never drop below the threshold.

For each 15-minute period, the planner applies every matching rule:

- **Heating** (cottage heaters): use the highest matching temperature, never below the base.
- **Cooling** (home pumps in summer): use the lowest matching temperature, never above the base. A rule may also switch a unit off, e.g. above 30 c/kWh.

Consecutive periods with the same setpoint merge into one change. Setpoints stay inside each device's reported range; the cottage floor is 5 °C.

Example cottage rule set (the owner sets the real values):

| Rule | Setpoint |
| --- | --- |
| Base | 5 °C |
| Cheapest 16 quarter-hours of the day | 8 °C |
| Price below 5 c/kWh | 12 °C |

This heats in the cheap periods and lets the cottage cool towards 5 °C in expensive ones. The heaters' own thermostats handle the rest.

## Efficiency

Electric panel heaters convert power to heat at the same efficiency at any hour. The cost of storing heat is extra heat loss while the cottage is warmer than the base. Higher boosts pay off when the cheap period is close to an expensive one. The app helps the owner tune the rules rather than deciding for them:

- The plan view shows tomorrow's prices and setpoints side by side.
- Once ALPSTUGA history exists, the app shows the measured heating and cooling rates (°C per hour), so the owner can see how long a boost lasts and whether the 12-hour heat-up estimate holds.

A rule that preheats specifically before a long expensive run is a later addition, if the simple rules leave clear savings.

## Visit mode

A button in the app: **Aloita vierailulämmitys** sets all cottage heaters to 22 °C at once and suspends the price rules. **Lopeta vierailu** returns to the price rules. Heat-up from the base takes an estimated 12 hours or more; the app will show the measured time once sensor history exists.

## Where the plan runs

The PC computes all plans, so there is one rule engine and one place to view them.

| Site | Executor |
| --- | --- |
| Home pumps | PC sends setpoints to the units directly, at each change |
| Cottage heaters | Once a day after prices publish, the Android app fetches tomorrow's plan from the PC and writes it into Google Home automations with clock-time starters. Google runs them; the PC and phone can be off. See [platform decision](platform-decision.md). |

Each cottage plan ends with a return to the base temperature at midnight, so a missed update leaves the cottage at 5 °C rather than at a boost temperature. Visit mode disables the uploaded automations and re-enables them when the visit ends.

## Not in the first version

Weather forecasts, learned thermal models and power metering.
