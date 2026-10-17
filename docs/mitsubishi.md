# Mitsubishi LN25 / LN35 case study

## Finding

Confirmed by the owner: the Mitsubishi pumps are at the main residence, not the cottage. Their local adapters can communicate with the home PC over the home LAN; cottage WAN and Thread routing constraints do not apply to this integration.

Local control is plausible, but “LN25/LN35” is insufficient to select the integration. Record the complete indoor-unit suffix, region, Wi-Fi adapter model and firmware, and whether the installed app is MELCloud or MELCloud Home. These are distinct cloud integrations.

| Route | Cloud independence | Evidence / next test |
| --- | --- | --- |
| Existing adapter using ECHONET Lite | Potentially local at runtime | [ECHONET integration](https://github.com/scottyphillips/echonetlite_homeassistant) lists specific adapters, including MAC-568IF-E with LN25VG2/LN35VG2. This does not establish European adapter support. Identify installed adapter first. |
| CN105 serial adapter, e.g. ESP32 + ESPHome | Local runtime; strongest fallback | Existing implementation and model-specific reports. Requires compatible connector, electrical design and installation. |
| MELCloud Home API | No | [HA integration](https://www.home-assistant.io/integrations/melcloud_home/) uses the manufacturer's cloud. Useful only if the owner accepts a temporary bridge. |
| Emulated manufacturer server | Unknown | No verified trust/provisioning path for these units in this research. Do not plan delivery around it. |
| Infrared controller | Local but limited feedback | Possible fallback; commanded state may diverge when other remotes are used. Inferior where verified bidirectional control is available. |

The [LN25/LN35 owner report](https://github.com/echavet/MitsubishiCN105ESPHome/discussions/317) names MSZ-LN25VG2W and MSZ-LN35VG2W working on CN105. This is useful compatibility evidence, not a guarantee for another suffix. A [newer-adapter discussion](https://github.com/echavet/MitsubishiCN105ESPHome/discussions/681) concerns MAC-597IFB-E and MELCloud Home; firmware/region assumptions must not be carried across adapters.

## Feature boundary

Start with power, heat/cool mode, target temperature, current temperature, fan setting and availability. Test vane controls separately. Do not promise i-see sensor features, independent split vanes, energy reporting, low-temperature maintenance mode, schedules or full app parity without observed support.

The current [ESPHome component](https://esphome.io/components/climate/mitsubishi_cn105/) documents UART control/state reporting and a 16–31 °C target range. A requirement for 8–10 °C maintenance heating is therefore a separate acceptance gate; do not silently clamp it to 16 °C or assume another component implements it for this pump.

## First experiment

1. Identify one unit and its adapter; collect model/firmware facts privately. No serial number or pairing QR code is needed in the public repo.
2. Determine whether its existing adapter offers a supported or already implemented local path. Inspect existing projects before reverse engineering the app.
3. If not, select an exact CN105 adapter/harness and verify compatibility with service documentation. Installation should be performed with power isolated by someone competent for the equipment. Do not assume cable colors or a generic UART voltage are correct. Check whether the current Wi-Fi module occupies the port and whether coexistence is supported; do not connect two controllers with an improvised Y cable.
4. Read state first. Compare readings and supported modes against the physical remote.
5. With the owner present, apply one small setpoint change, observe physical response and read-back, then restore the original setting.
6. Test physical remote changes, adapter reboot, service reboot, Wi-Fi loss and loss of vendor internet access. Block vendor access only after basic operation is proven and preserve rollback.
7. Record a capability table, tested firmware, adapter revision, observed delays and failure behavior. Keep the original module available for restoration.

Acceptance: device-reported state converges after commands and remote changes; no stale command is applied after an outage; the pump continues autonomous thermostat operation without the PC or manufacturer cloud. Set a measurable convergence target after observing the hardware, provisionally within 10 seconds for basic state.

## If REA is needed

Analyze the exact app version for LAN discovery, local endpoints, provisioning and authentication logic. Use sanitized captures from the owner's own device/account only after live testing is authorized. Establish what the device speaks separately from what the phone app speaks. A cloud REST client in the app is evidence of a cloud route, not evidence of a usable local protocol.

Stop the emulation branch if the firmware requires a manufacturer trust relationship that cannot be provisioned locally through an acceptable method. Reassess hardware replacement or an explicitly accepted cloud bridge.

Research date: 17 October 2026. No pump has been accessed or tested.
