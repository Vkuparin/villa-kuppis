# Questions before implementation

Research and documentation can proceed with these open. Hardware purchases, live-device testing and architecture decisions depend on the relevant answers.

## Highest priority

1. **Pumps (deferred until the owner can check):** complete indoor-unit and Wi-Fi adapter model numbers, firmware if available, and number of units? Main residence is confirmed. MELCloud or MELCloud Home exactly? Do not send pairing codes, serial numbers or passwords. Continue unrelated design work without repeating this request.
2. **Hardware changes:** willing to install a proven CN105 adapter, preferably ready-made, if factory Wi-Fi has no local route? Is preserving MELCloud alongside the new app necessary?
3. **Cottage constraint:** is “no computer” an absolute constraint, or a first-stage preference? If exact routers cannot provide the required access, prefer limited/cloud-assisted integration or different networking hardware? A small controller remains a later option only.
4. **Connectivity, if a direct VPN is needed:** 5G operator/APN and availability of public IP service; home router and internet type; BE230 hardware version/firmware? Are both sites behind CGNAT? Answers need no public IP addresses. These are not prerequisites for the standard Google remote-control experiment.
5. **PC availability:** Windows 11 and normally powered on are confirmed. Still decide automatic startup/recovery, sleep policy, willingness to use a phone VPN for home-PC access and required behavior while the PC is off. All household mobile devices are Android.
6. **Other services:** Google Home APIs and Google's existing hub/cloud for the cottage are accepted. Desktop/server access remains to be proven. Separately, are Tailscale coordination/relay, notifications, a small VPS or one-time vendor provisioning acceptable?
7. **Reuse:** is Home Assistant acceptable underneath your custom Finnish UI, or must the server also be independent of HA? Reusing protocol libraries is the recommended baseline either way.

**Desktop access constraint:** no required Premium subscription in production. The speaker includes a six-month Premium trial, with tier unverified. MCP remains an optional experiment only if entitled. Select a sustainable PC transport; see [platform decision](platform-decision.md).

## Heating behavior and product scope

8. Required Mitsubishi functions: basic settings, vanes, i-see, low-temperature maintenance heat, energy history or schedules? Rank the first three.
9. Are ALPSTUGA readings for monitoring or intended to regulate the Mill heaters? Which rooms contain the four heaters and two sensors, using generic room labels only?
10. What must happen at the cottage when PC, WAN, Wi-Fi or power fails? Is a minimum temperature needed to protect water pipes? Is there an independent temperature alarm or backup heat?
11. First useful scenario: daily home AC control, preheating before arriving at the cottage, away-temperature schedules, or electricity-price optimization? Price control should follow reliable basic control.
12. Users and permissions: just you, household members, guests? Need remote alerts, and through what channel?
13. How much new hardware/hosting cost and maintenance is acceptable? Any other devices that strongly influence the architecture?
14. Is Europe/Helsinki correct for both sites? Should technical design docs stay English while UI text is Finnish? The owner selected Apache License 2.0 when creating the public repository.

## Already settled

- Project display name: Villa Kuppis; proposed GitHub slug: villa-kuppis under the authenticated personal account.
- Public repository explicitly requested; UI language Finnish.
- Public repository created by the owner with Apache License 2.0; preserve that license.
- Server on a normally powered-on Windows 11 home PC; desktop and out-of-home phone access.
- All household mobile devices are Android. Google hub/cloud dependence for cottage access is acceptable.
- Avoid a required Premium subscription after the speaker's six-month trial expires.
- Cottage hardware inventory recorded in [cottage.md](cottage.md); no cottage computer initially.
- This turn creates research, tentative design documents and a project scaffold, not a deployed controller.
