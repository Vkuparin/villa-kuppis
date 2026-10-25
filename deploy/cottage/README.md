# Cottage Home Assistant

The cottage HP MP9 G4 (`cottage-server`) runs Debian 13 with Home Assistant OS as a virtual machine ([deploy/haos-vm.md](../haos-vm.md)); everything below happens inside that VM. Villa Kuppis (at home) sends each room's target over Tailscale; the `villa_kuppis_cottage` custom integration in this repository follows the room's ALPSTUGA locally and falls back to a safe setpoint on its own. Device setup and commissioning: [docs/cottage.md](../../docs/cottage.md).

## What the integration does

Every minute, and whenever a sensor or heater reports, each room is in one of four modes:

| Mode | When | Heaters |
| --- | --- | --- |
| `manual` | Someone changed a heater by hand (the heater, Google Home or the Home Assistant UI) | Left alone for `manual_hold_minutes`, or until `clear_manual` |
| `fallback` | No target from Villa Kuppis, or it expired | Own thermostats at `safe_setpoint` |
| `thermostat` | Valid target, but the ALPSTUGA reading is missing, stale or implausible | Own thermostats at the target |
| `follow` | Valid target and a fresh room reading | Target + `boost` below the target, `low_setpoint` above it, with hysteresis and minimum dwell |

Targets below `safe_setpoint` are raised to it and targets above `max_target` are capped. Each heater gets at most one command a minute and `max_commands_per_hour` an hour. The target and its expiry survive a Home Assistant restart; a restart is never taken for a manual change.

The integration has no cloud or network access of its own. Without the home server, Tailscale or the internet, the target expires and the room drops to `fallback`.

## Stand-in devices until the real ones arrive

`custom_components/villa_kuppis_simulator` gives the cottage Home Assistant heaters and sensors that look like what the Matter integration gives for the Mill Gen4 and the ALPSTUGA: `climate.sim_mill_<room>_<n>` (modes off/heat, 5–35 °C in 0.5 °C steps, own thermostat, `hvac_action`, a 2 s command delay, refuses commands when unreachable but keeps heating) and `sensor.sim_alpstuga_<room>_temperature`, `_humidity`, `_carbon_dioxide` and `_pm25` (written on change and at least every 5 minutes). Each room is one thermal mass losing heat to the outdoor temperature (by default the Met.no `weather.forecast_home`); the values are in [packages/villa_kuppis_simulator.yaml](packages/villa_kuppis_simulator.yaml) and the state survives restarts. Everything carries `simulated: true` and the names start with "Sim".

To rehearse failures: `villa_kuppis_simulator.set_available` takes entities off the network and back (one ALPSTUGA's readings go together), `villa_kuppis_simulator.set_room_temperature` jumps a room's temperature. A change to a stand-in heater in the Home Assistant UI is a hand change, like one in Google Home.

**Switching a room to the real devices:** in `/config/packages/villa_kuppis.yaml`, replace the room's `sim_` entity ids with the real ones, delete `/config/packages/villa_kuppis_simulator.yaml` and `/config/custom_components/villa_kuppis_simulator`, and restart Home Assistant. Villa Kuppis rebinds the room by itself: each status sensor publishes its room's devices (`devices` attribute) and Villa Kuppis binds them as `<room>-temperature`, `-humidity`, `-co2`, `-pm25` and `<room>-heater-N`. The device ids stay, so the room's history continues across the switch (the simulated part included).

## Install

1. In the Terminal & SSH app of the cottage Home Assistant, run
   `curl -fsSL https://raw.githubusercontent.com/Vkuparin/villa-kuppis/main/deploy/cottage/install.sh | sh -s -- --with-simulator`.
   It copies the integrations to `/config/custom_components/`, adds [packages/villa_kuppis.yaml](packages/villa_kuppis.yaml) (and the simulator's package) unless they are already there, loads packages in `configuration.yaml` (backup next to it), checks the configuration and restarts Home Assistant. Run it again without `--with-simulator` to update the integration later; edited packages are kept.
2. Each room gets `sensor.villa_kuppis_<room>_target`; its attributes show the mode, the reason, the target's revision and expiry, and the room's devices.
3. Create a long-lived access token for a dedicated, non-admin Home Assistant user `villa-kuppis` and put it, with `cottage_url: wss://cottage-haos.corvus-stairs.ts.net/api/websocket`, in the options of the Villa Kuppis app at home (`cottage_token`). Villa Kuppis binds the rooms' devices itself.

## Services

- `villa_kuppis_cottage.set_target`: `room`, `target` (°C), `revision` (must not decrease; Villa Kuppis uses the planning time in epoch seconds), `valid_minutes` (default 45). Sending the same revision again is a heartbeat.
- `villa_kuppis_cottage.clear_manual`: `room`. Ends a manual pause at once.

## Tests

- Decision logic and 24-hour room simulations: `python -m unittest discover -s tests -p "test_cottage_control.py" -v` (main environment, no Home Assistant).
- The integration inside Home Assistant core, in a separate environment: `pip install -r tests_ha/requirements.txt`, then `python -m pytest -c tests_ha/pytest.ini tests_ha`.

Still to do with the real devices (H2.1, H2.2): check how often the ALPSTUGA reports (`sensor_max_age_minutes`), the heaters' real minimum and step over Matter, how a Google Home change shows up in Home Assistant, and an owner-approved test with the WAN unplugged.
