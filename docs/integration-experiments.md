# First integration experiments

These are runnable experiment specifications, not reports of completed tests. Scope is deliberately one heater, one sensor and one pump before expanding. Keep private records under ignored `scratch/` on the owner's machine; public results contain generic device labels and sanitized capability facts only.

## A. Android cottage access

Prerequisites: an Android phone, the cottage devices commissioned in Google Home, the speaker online, and the owner's explicit go-ahead for the named live-device test. Work from a pinned revision of [Google's Android sample](https://github.com/google-home/google-home-api-sample-app-android). Record SDK and Android versions.

1. Create the project/OAuth configuration on the owner's account and use the standard `home.platform.selected.devices` scope. The current sample's v2 default is partner-gated; follow [the authorization guide](https://developers.home.google.com/apis/android/sample-app/authorization). Store signing keys and credentials outside Git.
2. Add household test accounts, grant the intended cottage structure, and enumerate actual capabilities. Add a minimal sensor/thermostat view if the sample does not already render them. Do not confuse a missing sample screen with a missing API capability.
3. Read one ALPSTUGA temperature and one Mill heater's measured temperature, target and mode. Record exact trait names, units, allowed range/step and observation timestamps when available. Distinguish the sensor reading from the heater's internal sensor.
4. Disable phone Wi-Fi and repeat over cellular data. Read state from the cottage while the phone is elsewhere. Keep the home PC powered off for this check: Google-mediated cottage access should not require it.
5. With a person able to observe the heater, record its original target, request one small supported setpoint change, confirm via device state and physical observation, then restore the original value. Do not infer room heating from immediate setpoint confirmation.
6. Test permission revocation and a controlled loss of hub/WAN connectivity. Display unavailable or dated state, reject new control where appropriate, and check reconnection without replaying old commands. Do not interrupt cottage heating or networking remotely without someone present to recover it.

Pass: the intended household account can read the sensor and control/read back the heater over cellular using supported permissions. Failure includes missing required traits, unavailable account access, or remote access working only in Google's own app. Report command convergence measurements; a provisional UI target is confirmation within 10 seconds, subject to actual hardware behavior.

## B. PC cottage access through MCP, only if selected

Scope update: optional trial research only. The owner requires production to work without Premium. Do not treat a passing MCP experiment as completion of the production desktop-access gate. Verify the six-month trial includes Advanced before attempting this route.

Prerequisites: explicit selection of this paid candidate, verified subscription/region/account eligibility, provider access and approved authentication setup. Do not purchase anything or enable billing to make a test pass.

1. Authenticate a minimal MCP client from the home PC. Verify available tools and discover only the intended cottage resources.
2. Read one sensor and heater. Record freshness, connectivity, timestamp behavior and the discovered command schema. Preserve raw OAuth tokens and IDs privately.
3. Request one owner-authorized setpoint change with the discovered schema, inspect the per-action outcome, read state and restore the original setting. No AI model chooses the target or value.
4. Turn off all household phones and repeat a read from the PC. Restart the PC process and verify credential recovery without a phone acting as a relay.
5. Simulate request timeout and token expiry in local fixtures before repeating any potentially uncertain real command. Test revoked access and unavailable device behavior without declaring cached data fresh.

Pass: independent PC control and observation work for the actual device traits, and the integration survives ordinary process/authentication lifecycle events. If unavailable, record the precise gate and leave desktop cottage controls unimplemented rather than fabricating success.

## C. Mitsubishi at home

First obtain complete indoor-unit and Wi-Fi adapter model numbers. Follow [the Mitsubishi experiment](mitsubishi.md) to choose an existing local protocol or a compatible CN105 adapter. This experiment does not depend on Google, the cottage or Matter routing. No hardware purchase is selected yet.

## Result record

Use a short private record per run: date, tested app/firmware versions, generic device label, transport, preconditions, expected behavior, observed result, duration, rollback outcome and remaining uncertainty. A failed or unavailable feature remains explicit. Publish only summarized results stripped of account, network, pairing and resource identifiers.

## Exit decision

| Evidence | Next action |
| --- | --- |
| Android and a PC path that does not require Premium pass | Select client stack and implement one shared Finnish device-control workflow |
| Android and trial MCP pass only | Record feasibility evidence; the production desktop transport remains unresolved |
| Android passes; PC path unresolved | Ask whether to stage mobile first or investigate another PC transport |
| Basic device traits unavailable | Reassess local REST/Matter route before building the app around Google |
| Mitsubishi local control passes first | Build the home-pump workflow independently while cottage research continues |

The source repository can be published before these live experiments. Passing mock tests does not establish compatibility or reliability of the real hardware.
