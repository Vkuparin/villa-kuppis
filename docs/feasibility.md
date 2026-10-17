# Feasibility

## Assessment

A bespoke Finnish interface and PC-hosted control service are feasible. Combining devices with different protocols is normal adapter work. Guaranteeing cloud-free control for every arbitrary device is not realistic: some devices have a local protocol, some need replacement hardware, and some remain tied to a vendor backend.

After the owner's clarification, standard Android Home APIs are the first cottage option to investigate: reuse the existing Google hub's remote functionality. Google Home MCP is an optional paid-access experiment during the speaker's trial, subject to entitlement; it is excluded as an essential production dependency. The largest uncertainty is reliable desktop cottage access without Premium. Direct Google-independent cottage access remains a separate routing challenge; adding a VPN to the home PC alone does not solve it. See [the revised cottage plan](cottage.md) and [platform decision](platform-decision.md).

## Three independent problems

1. **Replace the app:** build our own interface while calling an existing vendor API. This may improve usability but retains the vendor cloud.
2. **Control locally:** talk to a LAN protocol or a local hardware adapter. This can remove the vendor cloud from day-to-day control.
3. **Replace a device's cloud server:** implement the device-facing service and make the firmware accept it. This needs evidence of configurable endpoints or another viable trust/provisioning mechanism. DNS redirection alone does not solve TLS certificate validation, certificate pinning, client certificates or signed commands. These are possible obstacles, not findings about these particular pumps.

Use route 2 where available. Treat route 3 as a bounded research experiment for a specific device and firmware, with a stopping point.

## Implementation choices

| Option | Benefit | Cost / limitation |
| --- | --- | --- |
| Bespoke UI over Home Assistant | Reuses device integrations, events and operational tooling | Still maintains HA and an app integration boundary |
| Bespoke UI and small service using existing protocol libraries | Strong fit to this hardware and UI; fewer unrelated features | We own retries, state, scheduling, authentication, history and recovery |
| Entirely new protocol stacks and automation platform | Maximum control | Much larger maintenance and test burden; no current justification |

Tentative preference: custom UI and domain API, with adapters that may initially use Home Assistant or existing libraries. The owner must choose whether HA as an internal component is acceptable. Do not infer that a bespoke interface requires rewriting Matter, Thread or Mitsubishi serial protocols.

## Reverse Engineer Anything (REA)

[REA's upstream project](https://github.com/morluto/rea) documents APK/JADX analysis, adb observations, saved network captures and firmware analysis. It can help identify app endpoints, request formats and local-protocol code paths. It cannot recover the manufacturer's remote server simply by reading its app, or make the device trust an arbitrary server.

REA was researched but not installed or run in this session; no app package, device capture or firmware was supplied. There was no callable REA integration. Before an experiment, pin an upstream revision, identify the exact app/package version, record hashes and analysis-tool versions, and keep the artifacts private. Publish original implementation notes and sanitized evidence, not vendor binaries or account tokens.

A useful investigation asks one falsifiable question, such as: “Does this adapter expose a local command path after cloud access is blocked?” Separate static inference from observed traffic and a successful physical-device test. Timebox app/cloud emulation research to one or two focused sessions before deciding whether a local adapter is simpler.

## Scope and effort

The first useful milestone is one pump with verified local state and controls, not a whole-house platform. A mock UI can be quick; hardware validation, power-cycle behavior and multi-site recovery dominate uncertainty. Planning allowance: a few focused sessions for inventory and connectivity spikes, then several iterations for a one-pump MVP. No delivery date is supportable until hardware and network access are tested.

Future integrations should record protocol, firmware, local/cloud dependency, available capabilities, persistence through power loss, a rollback path and a repeatable acceptance test. Local APIs may still require vendor-assisted onboarding or updates; evaluate provisioning independence separately from runtime independence.

## Current evidence

- Mitsubishi: local UART control is implemented in [ESPHome CN105](https://esphome.io/components/climate/mitsubishi_cn105/); exact unit compatibility remains to be established.
- Mill: [Gen 4 product documentation](https://millnorway.com/product/mill-invisible-smart-wifi-panel-heater-1050w-low-model-gen-4/) advertises a local REST API. Endpoint and firmware tests are still required.
- Matter: [Home Assistant's implementation guidance](https://www.home-assistant.io/integrations/matter/) depends on local IPv6 and mDNS. A routed WAN design is a feasibility experiment, not an assumed supported deployment.

Sources accessed 17 October 2026. No claims of live hardware verification.
