# Spot-price automation

High priority. The owner sets price thresholds and temperatures; the PC applies them for every 15-minute price period, at both sites.

## Price data

- The raw Finnish day-ahead spot price (bidding zone FI), in 15-minute periods, with no VAT, margin or transfer fees added. Shown in c/kWh.
- Next-day prices are usually published around 14:00 Finnish time.
- Primary source: [ENTSO-E Transparency Platform](https://transparency.entsoe.eu/) (free API token). Backup: Elering's public API, which needs no key. Cache prices in SQLite.

## Rules

Each site, or group of devices, has a **base** setpoint and a list of thresholds: "when the spot price is below X c/kWh, use T".

For each 15-minute period, the planner applies every matching threshold:

- **Heating** (cottage heaters): use the highest matching temperature, never below the base.
- **Cooling** (home pumps in summer): use the lowest matching temperature, never above the base. A rule may also switch a unit off, e.g. above 30 c/kWh.

Example cottage rules (the owner sets the real values):

| Rule | Setpoint |
| --- | --- |
| Base | 5 °C |
| Spot price below 8 c/kWh | 8 °C |
| Spot price below 5 c/kWh | 12 °C |

This heats in cheap periods and lets the cottage cool towards 5 °C in expensive ones. The heaters' own thermostats do the rest. The PC sends a new setpoint only when it changes between periods. Setpoints stay inside each device's reported range.

The plan view shows today's and tomorrow's prices with the planned setpoint for each period.

## Visit mode

**Aloita vierailulämmitys** sets all cottage heaters to 22 °C and suspends the price rules. **Lopeta vierailu** returns to them. Nothing ends a visit automatically.

## Later, if wanted

- A "cheapest hours" rule: in the cheapest N hours of each day, use T, whatever the actual price. This would give the cottage a boost even on winter days when the price never drops below a threshold.
- Measured heat-up and cool-down rates from the heaters' own temperature readings, to tune the thresholds and the visit lead time.
