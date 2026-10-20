# Cottage Home Assistant

The cottage HP MP9 G4 runs Home Assistant OS. Villa Kuppis (at home) sends each room's target over Tailscale; the `villa_kuppis_cottage` custom integration in this repository follows the room's ALPSTUGA locally and falls back to a safe setpoint on its own. Device setup and commissioning: [docs/cottage.md](../../docs/cottage.md).

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

## Install

1. Copy `custom_components/villa_kuppis_cottage/` from this repository to `/config/custom_components/` on the cottage Home Assistant (Samba share or the File editor / Studio Code Server app). HACS should also work (add this repository as a custom integration repository; not tried yet).
2. Copy [packages/villa_kuppis.yaml](packages/villa_kuppis.yaml) to `/config/packages/`, enable packages in `configuration.yaml` (see the file's header) and replace the placeholder entity ids with the real ones.
3. Restart Home Assistant. Each room gets `sensor.villa_kuppis_<room>_target`; its attributes show the mode, the reason, the target's revision and expiry.
4. Create a long-lived access token for a dedicated, non-admin Home Assistant user `villa-kuppis` and store it on the home server outside Git (H2.3).

## Services

- `villa_kuppis_cottage.set_target`: `room`, `target` (°C), `revision` (must not decrease; Villa Kuppis uses the planning time in epoch seconds), `valid_minutes` (default 45). Sending the same revision again is a heartbeat.
- `villa_kuppis_cottage.clear_manual`: `room`. Ends a manual pause at once.

## Tests

- Decision logic and 24-hour room simulations: `python -m unittest discover -s tests -p "test_cottage_control.py" -v` (main environment, no Home Assistant).
- The integration inside Home Assistant core, in a separate environment: `pip install -r tests_ha/requirements.txt`, then `python -m pytest -c tests_ha/pytest.ini tests_ha`.

Still to do with the devices (H2.1, H2.2): check how often the ALPSTUGA reports (`sensor_max_age_minutes`), the heaters' real minimum and step over Matter, how a Google Home change shows up in Home Assistant, and an owner-approved test with the WAN unplugged.
