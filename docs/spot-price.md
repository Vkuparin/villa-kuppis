# Spot-price automation

High priority. Plans are computed on the PC for every 15-minute price period. The home pumps get them directly; the cottage gets them through the Android app's daily upload.

## Inputs

- **Prices:** the raw Finnish day-ahead spot price (bidding zone FI), with no VAT, margin or fees, in c/kWh. Next-day prices are usually published around 14:00 Finnish time. Source: Elering's public API (`dashboard.elering.ee/api/nps/price`, no key; returns Finnish 15-minute prices in €/MWh without VAT, so divide by 10 for c/kWh). Optional backup: [ENTSO-E Transparency Platform](https://transparency.entsoe.eu/), free, but its token needs registration and an access request to transparency@entsoe.eu.
- **Outdoor forecast:** the Finnish Meteorological Institute's open data WFS (`opendata.fmi.fi/wfs`, no key), for the optimizer.
- **History:** room readings (ALPSTUGA at the cottage, unit sensors at home) and setpoints, stored on the PC.

## Scope

Every setting can apply to a whole site or to a single room; a room setting overrides the site setting.

## Mode 1: fixed thresholds

A base temperature and thresholds: "when the spot price is below X c/kWh, use T". For heating, the highest matching temperature wins, never below the base. For cooling, the lowest wins, never above the base; a rule may also switch a unit off.

Example for the cottage:

| Rule | Target |
| --- | --- |
| Base | 5 °C |
| Spot price below 8 c/kWh | 8 °C |
| Spot price below 5 c/kWh | 12 °C |

## Mode 2: smart maintenance heating (Älykäs ylläpitolämmitys)

A switch per site or room. The owner sets:

- **Minimum temperature:** never go below this, whatever the price.
- **Maximum storage temperature:** never heat above this to store cheap energy.
- **Low price:** below this, storing heat is worth it.
- **High price:** above this, avoid heating and let the room cool towards the minimum.

The optimizer chooses a target for each 15-minute period that minimizes the cost of the known price window while staying between the minimum and the maximum. It uses each room's learned heat-up and cool-down rates and the outdoor forecast. Until enough history exists (a few weeks of readings), it falls back to the fixed thresholds implied by the low and high prices.

**Cooling** works the same in reverse for the pumps: a maximum temperature, a minimum precooling temperature, and low and high prices.

## Visit heating

The owner enters an arrival time and a target temperature, e.g. Friday 18:00 and 22 °C. The planner picks the cheapest periods that still reach the target by arrival:

- Until each room's heat-up rate is learned, it assumes the heat-up from maintenance temperature takes 12 hours.
- If arrival is beyond the published prices, it plans with the prices it has and recalculates when the next day's prices arrive.
- The Android app uploads the visit plan at once, and again after each recalculation.
- The end-visit button returns the cottage to maintenance heating.
- A notification warns if the plan cannot reach the target in time.

## Plan view

Today's and tomorrow's prices with the planned target per period and per room, the estimated cost, and the reason for each change (threshold, optimizer, visit).
