# Mitsubishi LN25 / LN35

## Installation

At home. Not in Google Home.

| Indoor unit | Outdoor unit | Wi-Fi adapter | Route |
| --- | --- | --- | --- |
| LN35 | Outdoor unit 2 (shared) | MAC-577IF2-E | MELCloud Home cloud |
| LN25 upstairs | To confirm | MAC-597IFB-E | MELCloud Home cloud |
| LN25 downstairs | To confirm | Not yet checked | MELCloud Home cloud |

One LN25 is on outdoor unit 1 (single split); the other shares outdoor unit 2 with the LN35.

Supplied identifiers, one per indoor unit: `LN25VGHZ2LU`, `LN35VG2WLU`, `LN25VG2WLU`. All three can be operated independently. Two indoor units share outdoor unit 2. Multi-split outdoor units usually cannot heat one room and cool another at the same time; check this on the real units.

**Main use: summer cooling.** The apartment's radiators are geothermal and outside the owner's control.

## Routes

One route for every unit: the MELCloud Home cloud. All three units are in the owner's MELCloud Home app.

**MAC-597IFB-E (LN25 upstairs):** MELCloud Home's current adapter. Mitsubishi says it needs continuous internet; there is no known local API, and `pymitsubishi` does not support it. Cloud only.

**MAC-577IF2-E (LN35):** the previous generation, in use in the owner's MELCloud Home app. It also has a local `/smart` HTTP endpoint that [`pymitsubishi`](https://github.com/pymitsubishi/pymitsubishi) can control on the LAN. Not needed now; it is a local fallback for this unit if the cloud proves unreliable.

The MELCloud Home cloud clients:

- [`aiomelcloudhome`](https://github.com/erwindouna/aiomelcloudhome): async Python client for buildings, devices, air-to-air control and measurements, with a realtime websocket stream and polling as fallback.
- [`melcloudhome`](https://github.com/andrew-blake/melcloudhome): a Home Assistant integration with its own client, reverse engineered from the MELCloud Home web app. A second reference if the first lacks something.

Both are unofficial. No new hardware is needed, and the MELCloud Home app keeps working alongside. Keep each adapter on the LAN only; published research describes weak security in MAC-577 firmware.

**Local alternative, only if the cloud proves unreliable:** replace a MAC-597 with a CN105 serial board running [ESPHome](https://esphome.io/components/climate/mitsubishi_cn105/). An [owner report](https://github.com/echavet/MitsubishiCN105ESPHome/discussions/317) confirms MSZ-LN25VG2W and MSZ-LN35VG2W. This takes the adapter's connector, so MELCloud Home stops working on that unit.

## First functions

Power, mode (cool, heat, dry, fan), target temperature, room temperature, fan speed and availability. Vanes come after these work. The spot-price planner keeps setpoints inside the range the unit reports.

## First experiment

1. With the owner's go-ahead, sign in from the PC with `aiomelcloudhome` and list the units in MELCloud Home. Start with the LN25 upstairs (MAC-597IFB-E). Enter credentials in the probe's masked local prompts; they stay in memory and are not saved. Do not put passwords in chat, shell arguments or files.
2. Read one unit's state and compare it with the MELCloud Home app.
3. Change its setpoint by one degree, confirm the read-back, then restore it.
4. Change something in the MELCloud Home app and confirm the PC sees it.
5. Note whether the realtime stream works and any sign of rate limiting, to set a safe polling interval.

Done when one unit reads and sets reliably. Then check the other two.

### Probe commands

Install the pinned client through `requirements.txt`. Help and the synthetic demo use no account or network:

```powershell
.venv\Scripts\python -m villa_kuppis.pump_probe --help
.venv\Scripts\python -m villa_kuppis.pump_probe demo
.venv\Scripts\python -m unittest discover -s tests -p "test_pump*.py" -v
```

After live approval, use a local interactive terminal:

```powershell
.venv\Scripts\python -m villa_kuppis.pump_probe --live read
.venv\Scripts\python -m villa_kuppis.pump_probe --live test-setpoint
.venv\Scripts\python -m villa_kuppis.pump_probe --live test-power
```

The write test includes a read and unit selection, so the separate read command is optional. Keep MELCloud Home open to identify the upstairs unit and compare the cloud-reported state. The client does not expose a model/MAC field that can identify it automatically. Unit names appear only in the local terminal; credentials, tokens and device IDs are not written to a report.

The probe chooses a target 1 °C above the original, or 1 °C below if the upper limit prevents that. It requires reported bounds and a local confirmation before writing. It requests only the setpoint, then reads back the change and restores the original. It also checks whether power, mode, fan, vanes or standby changed. Unknown bounds, unsupported modes and reported errors prevent the write.

Each control request gets one HTTP attempt. A failed response still triggers read-back because the server may have applied the change. The probe must observe both the changed setpoint and the restored original. If both are confirmed and the other controls stay unchanged, a server response error produces a visible warning with the successful result. An unobserved target remains a failed test. Error messages identify the operation and, when available, an HTTP status without exposing the response body.

Restoration runs after an attempted write even if the request times out. Cleanup can make one further restoration attempt if the normal restoration was not confirmed. A read-back of the original resolves restoration; it does not turn an unobserved target change into a successful test.

If the probe prints `RESTORATION UNCONFIRMED`, check the app and restore the displayed original value. A forced process or PC shutdown can prevent cleanup; this first test is supervised. Realtime updates and app-side changes are follow-up checks after REST read/change/restore succeeds.

The pinned `aiomelcloudhome` 0.3.1 source sends `null` for unspecified control fields. The offline payload test verifies that setpoint is the only non-null requested change; the live check must establish the server's behavior. Source: [client and models](https://github.com/erwindouna/aiomelcloudhome).

The separately approved `test-power` starts only when the selected unit reports power off. It requests power on, confirms it by cloud read-back, waits 30 seconds, then requests and confirms power off. The timer starts from the cloud-reported state; it does not measure how long the physical unit operated. Only power is requested; the other controls are checked for drift. Keep the console open until off is confirmed. On interruption or an unconfirmed on-state, cleanup attempts to turn the unit off. If it prints `POWER OFF UNCONFIRMED`, turn the unit off in MELCloud Home and check its state.
