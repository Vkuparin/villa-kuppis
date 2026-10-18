# Spot-price automation

High priority. Shift heating into cheap periods at both sites without the owner having to think about it.

## Price data

- Finnish day-ahead prices (bidding zone FI) come from Nord Pool. Since 1 October 2025 the day-ahead market uses 15-minute periods.
- Next-day prices are usually published around 14:00 Finnish time.
- Free sources: [ENTSO-E Transparency Platform](https://transparency.entsoe.eu/) (needs a free API token) or a Finnish aggregator such as [spot-hinta.fi](https://spot-hinta.fi/) or [porssisahko.net](https://porssisahko.net/). Use one, keep a second as backup, and cache prices in SQLite.
- Planning only needs prices relative to each other. VAT and the contract margin do not change which hours are cheapest; a time-of-use transfer tariff does, so include it if either site has one.

## Policies

All parameters are owner-adjustable and have conservative defaults.

**Home pumps.** Each indoor unit has a comfort setpoint. In the cheapest periods, raise it by up to one degree; in the most expensive, lower it by up to one degree. Change a setpoint at most once an hour so the pumps are not nudged every 15 minutes.

**Cottage, away.** Stay in the 5–8 °C band: 8 °C in the cheapest periods, 5 °C in the most expensive, somewhere between otherwise. The heaters' own thermostats do the rest.

**Cottage, visit.** The owner gives an arrival time. The planner raises the heaters to 22 °C early enough to reach it, preferring cheap periods within that window. Start with a fixed lead time the owner sets. Learn the real heat-up time later from ALPSTUGA and heater readings.

## Where the plan runs

| Site | Executor |
| --- | --- |
| Home | PC service sends setpoints to the pumps directly |
| Cottage, Premium active | PC service sends setpoints through Google Home MCP |
| Cottage, no Premium | Android app writes the day's plan into Google Home automations; see [platform decision](platform-decision.md) |

Every plan is computed on the PC and shown in the UI before and while it runs: when each setpoint change happens and roughly what it saves.

## Not in the first version

Weather forecasts, learned thermal models, outdoor-unit power metering and summer cooling optimization. Add them only if the simple rules leave clear savings on the table.
