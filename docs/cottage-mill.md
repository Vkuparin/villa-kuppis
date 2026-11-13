# Mill control alternatives

Investigation, 5 November 2026 (H2.6). No live accounts, Home Assistant instances or heaters were accessed. The owner reports that Kammari's heater keeps an earlier setpoint even when Matter reports the requested value. The controller already records this failure pattern in [control.py](../custom_components/villa_kuppis_cottage/control.py).

## Recommendation

The built-in Home Assistant Mill cloud integration is a plausible alternative, but its displayed setpoint is not proof of delivery. Trial Kammari alone before changing the other heaters. First check whether its Gen 4 firmware works with HA's local Mill connection: that would bypass Matter and preserve control without cottage internet. Cloud is the next candidate if local access does not work.

| Route | Fit for Villa Kuppis | Unresolved |
| --- | --- | --- |
| Matter today | Local commands and ALPSTUGA control | Reported setpoint can disagree with the heater |
| Mill cloud | Built-in HA integration exposes ordinary climate entities | Actual Gen 4 discovery and delivery; cloud feedback can reflect desired state; WAN dependency |
| Mill local REST | Would keep the controller and heater commands local | Gen 4 compatibility with HA's documented Gen 3 implementation |

## Live findings, 5 November 2026

Kammari's Mill (firmware 0x250828-1-g6797788dd, the same on all four), with the owner at the heater reading its display:

- Over Matter every write is answered "success" and reported back, but some are not applied. Taken: 21:51, 22:19, 03:44, 04:19, 07:41, 07:44, 07:46. Dropped: 01:29 (5 °C, kept heating to 25 °C), 05:04, 05:30, 06:00 (19 °C, held 11 °C while the room fell from 23 to 15 °C), 07:47 and everything after it. The Matter server log looks the same for both.
- Not a fix: resending the same value, a changed value (20, 23), off and on (`systemMode` 0 then 4), or pressing the heater's buttons just before a write. Pressing its buttons at 07:29 and a power cycle at 03:48 were each followed by writes that took, but not reliably.
- Half degrees are lost over Matter (9.5 °C showed 9.0) although its buttons step 0.5 °C; integration 0.10.0 sends whole degrees.
- While on Matter, the heater (192.168.50.176) refuses TCP connections on port 80 within milliseconds: no local REST API in this state. It is not in the Mill app.
- Integration 0.10.1 judges each heater by its own sensor and, when one that should heat does not, lists it in `disobeying`; Villa Kuppis 0.4.53 sends a phone alert.
- Trial step, about 08:55: Kammari reset and paired in the Mill app, firmware 0x261023. Port 80 now answers: `GET /status` gives `"name": "Mill HeaterGen4Panel"`, `/control-status` gives ambient and set temperature, `current_power`, `operation_mode` ("Weekly program" after pairing) and `connected_to_cloud`, the same API as HA's local client (mill-local 0.5.0). HA's local entity maps Heat to "Control individually" (no program or timers), Auto to "Weekly program" and Off to off, with a 0.1 °C step and `hvac_action` from `current_power`.
- Integration 0.10.2 takes a heater that moves to Auto (its Mill program) back to Heat with its setpoint, without counting it as a hand change.
- 6.11.2026: Tienpuoli, the only working Mökki heater, ran flat out all night 0.5 °C under a 12 °C target and the trim learned that as a 7.5 °C sensor gap; after the target fell to 8 °C it kept heating a 9 °C room at a 14.5 °C setpoint. Integration 0.10.3 does not learn the trim while the heaters already run flat out (or stay off) the way the room asks, and a room more than 1 °C over its target never gets a setpoint above the heaters' own reading minus 1 °C. Integration 0.10.4 also restarts the trim from the heaters' own readings after a hand change, so a stale trim does not come back when the hold ends.
- 12.-13.11.2026: mild weather kept both rooms just over their 5 °C target with the setpoint already at its 5 °C floor, and the trim kept learning down (Kammari to the -10 limit, Mökki to -1). Then Mökki fell to 4.2 °C while Tienpuoli, reading 5.1 on its own sensor, got 5.0 °C and stayed idle. Integration 0.10.5 does not learn the trim while a warm room has the setpoint at the floor, and a room more than 1 °C under its target always gets a setpoint at least 1 °C above the heaters' own readings.

## What Home Assistant supports

HA's [Mill integration](https://www.home-assistant.io/integrations/mill/) offers cloud and local connections through Settings > Devices & services > Add integration > Mill. Cloud setup takes the Mill account's username and password; no HACS component or separate HAOS app is needed. This is Mill's cloud, not a requirement for a Nabu Casa subscription. The [2026.11.0 setup flow](https://github.com/home-assistant/core/blob/2026.11.0/homeassistant/components/mill/config_flow.py) contains both choices.

The [2026.11.0 climate platform](https://github.com/home-assistant/core/blob/2026.11.0/homeassistant/components/mill/climate.py) provides per-heater target temperature, heat/off, current temperature and heating/idle state. It advertises a 0.1 °C target step; the heater's real step must be measured. Use `climate.set_temperature` for individual heaters, rather than the separate room comfort/away/sleep action.

Mill's [Invisible 1050 W Gen 4 product page](https://millnorway.com/product/mill-invisible-smart-wifi-panel-heater-1050w-low-model-gen-4/) lists app control, an open API and a local REST API. HA's cloud client classifies devices by API type rather than a Gen 3 model whitelist, so Gen 4 cloud operation is a reasonable expectation, not verified compatibility with these four units. HA's local documentation and the [local client](https://github.com/Danielhiversen/pyMillLocal) explicitly describe Gen 3; Gen 4 advertising alone does not establish compatibility.

## The cloud read-back problem

The [2026.11.0 manifest](https://github.com/home-assistant/core/blob/2026.11.0/homeassistant/components/mill/manifest.json) pins `millheater==0.14.1`. Inspection of that release's [source archive](https://files.pythonhosted.org/packages/fb/1d/61ce0eef7bb409dcc0f60aa4e2d1deff51928ed7d4fe6bed058d767b0383/millheater-0.14.1.tar.gz) found:

- `set_heater_temp` sends `temperature_normal` with `operation_mode: control_individually`, then updates its in-memory setpoint and inferred heating state after an accepted response.
- `Heater.__post_init__` prefers `deviceSettings.desired.temperature_normal` over `lastMetrics.temperature`. A later refresh can therefore still show the requested value without confirming the device applied it.
- Some request errors are logged and return `None` without raising. A completed HA service call is not a delivery guarantee.

HA requests a refresh after a write and polls cloud every 30 seconds, local every 15 seconds ([setup source](https://github.com/home-assistant/core/blob/2026.11.0/homeassistant/components/mill/__init__.py)). The cloud library also caches responses; polling frequency does not guarantee fresh device telemetry. Recheck the installed HA version and its dependency before a trial. These findings are specific to the reviewed release, not the library's moving development branch.

Villa Kuppis currently accepts matching HA climate state as read-back. Its retry and rising-temperature checks cannot turn desired cloud state into physical confirmation. A reliable trial needs the heater display, fresh device telemetry where available, and observed heating behavior. The Mill app alone is another cloud view.

## Migration and fallback

Mill's [official FAQ](https://millnorway.com/mill-heaters-smart-devices-frequently-asked-questions-faq/) says Matter and Mill Cloud cannot operate simultaneously on one heater. Returning Gen 4 to Mill requires the heater's `rA` reset and pairing again in the Mill app. Plan this with someone at the heater; a return to Matter also needs commissioning. Keep the ALPSTUGAs on Matter/Thread.

Villa Kuppis's [cottage integration](../custom_components/villa_kuppis_cottage/__init__.py) uses generic climate services, so changing the heater entity should not need planner changes. Replace explicit heater IDs and exclude the old entity from area discovery. Each physical heater must have one active control entity. Keep a stable room and heater ordering for the published bindings; verify disabled-heater choices after replacement because those are stored by entity ID. Preserve wattage in the friendly name for the current energy estimate.

With cloud-only heater commands, a cottage WAN or Mill outage prevents HA from applying a new target, off command or fallback setpoint. The local controller can decide on 5 °C but cannot deliver it; the heater's retained behavior must be tested. A home-server/Tailscale outage with cottage internet still working is different: the cottage controller can continue sending cloud commands. Cloud-only control would therefore weaken the existing local-fallback design.

## One-heater acceptance trial

This is a proposed live trial, requiring the owner's go-ahead under AGENTS.md.

1. Record Kammari's model, firmware, configuration, physical setpoint and baseline heating behavior. Pause automatic commands to that heater for the trial and save its bindings.
2. Reset and pair it with Mill. Check available firmware updates. Check local API availability read-only and try HA's Local connection first; if incompatible, configure Cloud. Keep only the selected entity under Villa Kuppis control. Disable competing Mill/Tibber schedules for the trial.
3. Send modest whole- and half-degree changes in both directions, including 5 °C and heat/off/on. Record HA state, physical display, fresh device-reported values and time to application. Check that lowering the target actually reduces heating, allowing for residual warmth and PID behavior. Restore the baseline.
4. Exercise the cottage controller with its ALPSTUGA, then separately test home-link loss, cottage internet loss, reconnect and power recovery. Check for stale command replay and false manual holds. Restore baseline after each test.
5. Adopt local control if it passes. Consider cloud only if physical delivery passes and the owner accepts the measured outage behavior. A matching HA setpoint alone fails acceptance; leave the other three heaters unchanged until this is settled.

Validation for this investigation is source review and documentation checks only. No physical reliability, latency, Gen 4 local compatibility or outage behavior has been measured.
