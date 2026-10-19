# Spot-price automation

High priority. Plans are computed on the PC for every 15-minute price period. The home pumps get them directly; the cottage gets them through the Android app's daily upload.

## Inputs

- **Prices:** the raw Finnish day-ahead spot price (bidding zone FI), with no VAT, margin or fees, in c/kWh. Next-day prices are usually published around 14:00 Finnish time. The PC fetches them once a day at 14:25, and on demand with a **Hae hinnat** button (for example if the daily fetch found no prices yet). Plans are recomputed after each fetch. Days have 96 periods, but 92 or 100 on daylight-saving change days; timestamps are stored in UTC. Source: Elering's public API (`dashboard.elering.ee/api/nps/price`, no key; returns Finnish 15-minute prices in €/MWh without VAT, so divide by 10 for c/kWh). Optional backup: [ENTSO-E Transparency Platform](https://transparency.entsoe.eu/), free, but its token needs registration and an access request to transparency@entsoe.eu.
- **Outdoor forecast:** the Finnish Meteorological Institute's open data WFS (`opendata.fmi.fi/wfs`, no key), for the optimizer.
- **History:** room readings (ALPSTUGA at the cottage, unit sensors at home) and setpoints, stored on the PC.

## Scope

Every setting can apply to a whole site or to a single room; a room setting overrides the site setting. Every setting in this document, including the defaults below, can be changed in the app.

## Mode 1: fixed thresholds

A base temperature and thresholds: "when the spot price is below X c/kWh, use T". For heating, the highest matching temperature wins, never below the base. For cooling (the pumps), the lowest wins, never above the base; a rule may also switch a unit off.

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

The optimizer chooses a target for each 15-minute period, then merges them into at most 12 time windows per room for the cottage upload, that minimizes the cost of the known price window while staying between the minimum and the maximum. It uses each room's learned heat-up and cool-down rates and the outdoor forecast. Until enough history exists (a few weeks of readings), it falls back to the fixed thresholds implied by the low and high prices.

## Summer cooling (home pumps)

The pumps matter mostly for summer cooling, and they can be tested before the cottage devices arrive. Cooling mirrors heating:

| Setting | Default |
| --- | --- |
| Maximum temperature: cool at any price above this | 25 °C |
| Precooling temperature: cool down to this when the price is low | 21 °C |
| Low and high price | Set by the owner |
| Switch off when the price is high | Allowed |
| Minimum off time, to protect the compressor | 30 minutes |

- **Fixed thresholds:** as in mode 1, with the lowest matching temperature winning, never above the maximum. A rule may switch a unit off.
- **Optimizer:** as in mode 2, in reverse. It precools in cheap periods, lets rooms warm towards the maximum in expensive ones, and switches units off when that is cheapest. It uses each room's learned warm-up and cool-down rates and the FMI forecast.
- **Switching off:** a unit that was switched off comes back on when its room nears the maximum or the price drops, but not before the minimum off time.
- **Cooling only:** pump plans adjust only units that are in cooling mode and never change a unit's mode. In winter, rooms stay below the cooling setpoints and the pumps stay quiet. Because plans never change modes, the two units on the shared outdoor unit cannot be put in conflicting modes.

**Home away:** a separate button (`Poissa kotoa`) switches home to an away setting. The default is to cool only above 28 °C, with no precooling. Pressing it again restores normal cooling. It is independent of the cottage visit.

**Night mode:** in chosen rooms (bedrooms), the fan runs on its quietest setting during set night hours, even if cooling takes longer. Default hours: 22:00–07:00.

## Visit heating

The owner enters an arrival time and a target temperature, e.g. Friday 18:00 and 22 °C. The planner picks the cheapest periods that still reach the target by arrival:

- Until each room's heat-up rate is learned, it assumes the heat-up from maintenance temperature takes 12 hours.
- If arrival is beyond the published prices, it plans with the prices it has and recalculates when the next day's prices arrive.
- The Android app uploads the visit plan at once, and again after each recalculation.
- The end-visit button returns the cottage to maintenance heating.
- A notification warns if the plan cannot reach the target in time.

## Learned room rates

Card F5.1, in `villa_kuppis/learning.py`. Each room is a first-order model per hour: `dT/dt = a·(T_out − T) + b − c·cooling + h·heating`. `a` is how fast the room follows the outdoor temperature, `b` the steady drift (sun, people, the home's geothermal radiators), `c` and `h` what the unit adds while it actively cools or heats. The devices are their own thermostats, so a simulation holds the room at the setpoint once it gets there.

The fit uses the last 28 days of stored readings in 15-minute steps and ordinary least squares. It skips steps where the unit just holds its setpoint, steps whose controls changed, and modes it does not cover (dry, automatic). It needs a day of steps without the unit working; the unit's own rate needs two hours of active cooling or heating. Without outdoor data the `a` term is left out.

**Agreed error:** the model is trained on the days before the most recent complete day, then predicts that day from its first reading with the recorded controls and outdoor temperature. The day's mean absolute error must be at most 1.0 °C (the pumps report the room in 0.5 °C steps). The app shows the rates at the current conditions, the held-out result and a six-hour prediction with the unit off, using the FMI point forecast (`fmi::forecast::edited::weather::scandinavia::point::timevaluepair`, parameter `Temperature`).

## Cooling optimizer

Card F5.4, in `villa_kuppis/optimizer.py`; `cooling.py` applies it. The setting `Optimoitu jäähdytys` (on by default, per site or room) uses it once the room's cooling rate is learned; until then, in away mode or with the setting off, the fixed thresholds apply.

From the current period to the end of the known prices (today, and tomorrow once fetched) it picks a setpoint between the precooling temperature and the maximum, or off, for each 15-minute period. Off is allowed only where the fixed rule allows it (price at or above the high price, `Sammuta kalliilla hinnalla` on) and lasts at least the minimum off time. The cost is the compressor's share of each period at a nominal 1 kW plus a small fan load while on, so the estimates are relative. A room above the maximum carries a large penalty, so the plan stays within it whenever the unit can hold it. The cold left in the room when the prices run out is valued at the horizon's median price.

Dynamic programming on a 0.1 °C grid finds the plan, which is then simulated with the learned model and compared with the fixed-threshold plan simulated the same way; the cheaper one is used, so the optimizer never plans to cost more than the fixed thresholds. It replans at each new period from the room's measured temperature, and the same switch-off safeguards as the fixed thresholds apply. Tests run both on the recorded Elering days in `tests/fixtures`.

A unit brought back on because its room reached the maximum now holds the maximum until the expensive stretch ends, instead of being switched off again on the next pass.

## Plan view

Today's and tomorrow's prices with the planned target per period and per room, the estimated cost, and the reason for each change (threshold, optimizer, visit).
