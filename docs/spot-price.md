# Spot-price automation

High priority. Plans are computed on the PC for every 15-minute price period. The home pumps get them through the home device gateway; the cottage gets each room's current target through the cottage Home Assistant, which applies it locally and falls back to a safe maintenance setpoint if targets stop arriving (see [architecture](architecture.md#cottage-control)).

## Inputs

- **Prices:** the raw Finnish day-ahead spot price (bidding zone FI), with no VAT, margin or fees, in c/kWh. Next-day prices are usually published around 14:00 Finnish time. The installed service fetches tomorrow's prices from 14:25 and today's whenever they are missing; a failed attempt is retried every 15 minutes until the day is stored, and a stored day is never refetched. The **Hae hinnat** button in the plan section fetches any missing day on demand (at most once a minute). Without a price for the current period, the cooling plan keeps the maximum (or the away maximum) so power management and away mode keep working. Plans are recomputed after each fetch. Days have 96 periods, but 92 or 100 on daylight-saving change days; timestamps are stored in UTC. Source: Elering's public API (`dashboard.elering.ee/api/nps/price`, no key; returns Finnish 15-minute prices in €/MWh without VAT, so divide by 10 for c/kWh). Optional backup: [ENTSO-E Transparency Platform](https://transparency.entsoe.eu/), free, but its token needs registration and an access request to transparency@entsoe.eu.
- **Outdoor forecast:** the Finnish Meteorological Institute's open data WFS (`opendata.fmi.fi/wfs`, no key), for the optimizer.
- **History:** room readings (ALPSTUGA at the cottage, unit sensors at home) and setpoints, stored on the PC.

## VAT

Prices are the raw spot price by default. The setting `Lisää ALV sähkönhintaan` (off by default; the percentage defaults to 25.5 %) adds VAT everywhere a price is used: the price curve and its tooltip, the energy cost, and the cheap and high price thresholds of the cooling plan and optimizer. Thresholds are therefore entered as prices including VAT while it is on. The setting is per site like the others; the history graph uses the home value. Heating rules (cottage) will use the same price once the cottage automation exists, and the command-line plan printout does not apply VAT.

## Scope

Every setting can apply to a whole site or to a single room; a room setting overrides the site setting. Every setting in this document, including the defaults below, can be changed in the app.

## Mode 1: fixed thresholds

A base temperature and thresholds: "when the spot price is below X c/kWh, use T". For heating, the highest matching temperature wins, never below the base. For cooling (the pumps), the lowest wins, never above the base; a rule may also switch a unit off.

The cottage gets the current period's target per room through its Home Assistant (`villa_kuppis/cottage_targets.py`): the period start is the revision, each write is valid for 45 minutes, and the room's status sensor confirms it. With `Automaatio` off nothing is sent and the cottage drops to its local safe setpoint when the last target expires. Without a price for the period the base applies.

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

The optimizer chooses a target for each 15-minute period, that minimizes the cost of the known price window while staying between the minimum and the maximum. It uses each room's learned heat-up and cool-down rates and the outdoor forecast. Until enough history exists (a few weeks of readings), it falls back to the fixed thresholds implied by the low and high prices.

## Summer cooling (home pumps)

The pumps matter mostly for summer cooling, and they can be tested before the cottage devices arrive. Cooling mirrors heating:

| Setting | Default |
| --- | --- |
| Maximum temperature: cool at any price above this | 25 °C |
| Price control (`Hintaohjaus`): off / fixed thresholds / optimized | Optimized. Off: the target is always the maximum and the price is not used; use it in the heating season so cooling does not compete with radiators |
| Precooling temperature: cool down to this when the price is low | 21 °C |
| Low and high price (shown only with price control on) | 5 and 15 c/kWh |
| The automation may switch the pump on and off (`Automaatio saa käynnistää ja sammuttaa pumpun`) | On |
| Minimum time between power changes, to protect the compressor | 30 minutes |
| Manual pause (`Käsiohjauksen tauko`) | 3 hours |

One controller per unit decides each minute (audit 2026-10-20, section 2.1):

1. It leaves the unit alone while the automation is off, the unit is not in cooling mode, a command is waiting for confirmation, or the manual pause is on.
2. The target is the plan's: the maximum, the precooling temperature in a cheap period, the optimizer's target, or the away maximum. In an expensive period the target is the maximum.
3. With power control on, a unit that is on is switched off once its room is 0.5 °C below the target, and a unit that is off is switched on when its room is above the target. So in an expensive period a precooled room switches off and comes back only above the maximum. Every power change waits the minimum time after the unit's previous one. Nothing is started when there is no need to cool.
4. A unit that is on gets the target as its setpoint whenever they differ.

- **Fixed thresholds:** the lowest matching temperature wins, never above the maximum.
- **Optimizer:** precools in cheap periods, lets rooms warm towards the maximum in expensive ones, and plans switch-offs when that is cheapest. It uses each room's learned warm-up and cool-down rates and the FMI forecast.
- **Manual pause:** when the household changes a unit's power, setpoint or mode, in this app, in MELCloud Home or with the remote, the automation leaves that unit alone for the pause time. The card shows `Käsiohjaus klo 21.30 asti` and a `Palauta automaatio` button that ends the pause at once. A change counts as manual when someone other than `Suunnitelma` logged a command for the unit, or the unit changed with no command of the automation's to explain it. Fan changes do not pause. Pause 0 turns the pause off. To keep a unit off for good, set its mode to something other than cooling or turn power control off for that room.
- **Without prices:** the target is the maximum (the away maximum when away), so the temperature control keeps working.
- **Cooling only:** pump plans adjust only units that are in cooling mode and never change a unit's mode. In winter, rooms stay below the cooling setpoints and the pumps stay quiet. Because plans never change modes, the two units on the shared outdoor unit cannot be put in conflicting modes.

**Automation switch:** the `Automaatio` switch in the page header turns the whole price and temperature automation on or off. Off, it sends no commands (cooling, precooling, optimizer, power control, night mode); the devices stay as they are, including a unit the automation had switched off. The switch is stored with the data and starts off unless the service configuration says `"plan": true`. Turning it on ends every manual pause and applies the plan at once, then every minute. A changed setting reaches the pumps in the next pass.

**Settings migration (2026-10-25):** the old `Ohjaustapa`, `Esijäähdytys käytössä`, `Optimoitu jäähdytys`, `Sammuta kalliilla hinnalla` and `Ohjaa pumpun käyntiä lämpötilan mukaan` overrides are replaced once, at start, by `Hintaohjaus` and the power switch: precooling off becomes price control off and keeps only the old temperature switch; otherwise optimizer off becomes fixed thresholds, and either old power switch keeps power control on.

**Home away:** a separate button (`Poissa kotoa`) switches home to an away setting. The default is to cool only above 28 °C, with no precooling. Pressing it again restores normal cooling. It is independent of the cottage visit.

**Night mode:** in chosen rooms (bedrooms), the fan runs on its quietest setting during set night hours, even if cooling takes longer. Default hours: 22:00–07:00.

## Visit heating

The owner enters an arrival time and a target temperature, e.g. Friday 18:00 and 22 °C. The planner picks the cheapest periods that still reach the target by arrival:

- Until each room's heat-up rate is learned, it assumes the heat-up from maintenance temperature takes 12 hours.
- The start is chosen from up to 4 hours before `arrival − 12 h` so that the heat-up plus holding the warm room until arrival costs least (holding is assumed to take 30 % of full heating). Without prices for the whole window it starts at `arrival − 12 h`. A start that has happened is kept (`villa_kuppis/visit.py`).
- If arrival is beyond the published prices, it plans with the prices it has and recalculates when the next day's prices arrive.
- The 12-hour lead is only an assumption: in a hard frost the heaters may not reach the target at all, and the app must not show a visit as on time unless the room temperature shows it.
- Villa Kuppis sends the room targets to the cottage Home Assistant as they change, including after each recalculation.
- The end-visit button returns the cottage to maintenance heating.
- A notification warns if the plan cannot reach the target in time.

## Learned room rates

Card F5.1, in `villa_kuppis/learning.py`. Each room is a first-order model per hour: `dT/dt = a·(T_out − T) + b − c·cooling + h·heating`. `a` is how fast the room follows the outdoor temperature, `b` the steady drift (sun, people, the home's geothermal radiators), `c` and `h` what the unit adds while it actively cools or heats. The devices are their own thermostats, so a simulation holds the room at the setpoint once it gets there.

The fit uses the last 28 days of stored readings in 15-minute steps and ordinary least squares. It skips steps where the unit just holds its setpoint, steps whose controls changed, and modes it does not cover (dry, automatic). It needs a day of steps without the unit working; the unit's own rate needs two hours of active cooling or heating. Without outdoor data the `a` term is left out.

**Agreed error:** the model is trained on the days before the most recent complete day, then predicts that day from its first reading with the recorded controls and outdoor temperature. The day's mean absolute error must be at most 1.0 °C (the pumps report the room in 0.5 °C steps). The app shows the rates at the current conditions, the held-out result and a six-hour prediction with the unit off, using the FMI point forecast (`fmi::forecast::edited::weather::scandinavia::point::timevaluepair`, parameter `Temperature`).

## Cooling optimizer

Card F5.4, in `villa_kuppis/optimizer.py`; `cooling.py` applies it. `Hintaohjaus: Optimoitu` (the default, per site or room) uses it once the room's cooling rate is learned; until then and in away mode the fixed thresholds apply.

From the current period to the end of the known prices (today, and tomorrow once fetched) it picks a setpoint between the precooling temperature and the maximum, or off, for each 15-minute period. Off is allowed only where the fixed rule allows it (price at or above the high price, power control on) and lasts at least the minimum off time. The cost is the compressor's share of each period at a nominal 1 kW plus a small fan load while on, so the estimates are relative. A room above the maximum carries a large penalty, so the plan stays within it whenever the unit can hold it. The cold left in the room when the prices run out is valued at the horizon's median price.

Dynamic programming on a 0.1 °C grid finds the plan, which is then simulated with the learned model and compared with the fixed-threshold plan simulated the same way; the cheaper one is used, so the optimizer never plans to cost more than the fixed thresholds. It replans at each new period from the room's measured temperature, and the unit's controller applies its plan like the fixed one: a planned off period means the target is the maximum and the unit may be switched off. Tests run both on the recorded Elering days in `tests/fixtures`.

## Plan view

Today's and tomorrow's prices with the planned target per period and per room, the estimated cost, and the reason for each change (threshold, optimizer, visit).
