# First integration experiments

Experiment specifications and recorded checks. Keep private reference material in ignored `scratch/`; publish only generic device labels and capability facts. The pump probe reads credentials through masked local prompts and does not save them. Each live test needs the owner's go-ahead.

Order: C and D first, when implementation begins; A and B when the cottage devices are installed.

## C. Mitsubishi through MELCloud Home

Follow [the Mitsubishi experiment](mitsubishi.md#first-experiment). Runs on the home PC.

The supervised probe uses `aiomelcloudhome` 0.3.1. Its offline demo changes a synthetic unit from 23 °C to 24 °C and restores 23 °C.

Owner-approved live check on 19 October 2026 at `6087c3f`: the upstairs LN25 reported Cool mode, power off and a 16–31 °C setpoint range. A 1 °C change and restoration were both confirmed by independent cloud read-back; the other controls stayed unchanged. Both writes returned HTTP 500, so the completed probe reported a server-response warning. The owner observed the same failure-after-application behavior in the official mobile app.

The live transport makes one HTTP attempt per control request, then verifies state instead of blindly retrying a failed response. Earlier attempts could not confirm the target because they went straight to restoration after a write error. The final probe checked both states successfully; it does not establish the reason for MELCloud's HTTP 500 responses.

Offline validation at `62b43ea`: 29 focused pump tests, demo and help passed on Windows Python 3.13. Tests cover secure prompts, bounds, confirmations, ambiguous writes, installed-client HTTP behavior, restoration failure, interruption, control drift and sanitized diagnostics. Sol 6.1 Medium handled design, diagnosis and the final read-back repair; Kynsi implemented the initial probe and restoration repair. Document parsing and whitespace checks passed at the preceding checkpoint.

Owner-approved power test at `7861cda`: on and off were confirmed by independent cloud read-back and by the owner's visual check. The measured hold was 30.009 seconds from cloud-on confirmation; the owner observed a shorter physical on-time. Physical operating duration was not measured, so this does not validate 30 seconds of physical operation. Both writes returned HTTP 500, the other reported controls stayed unchanged, and the unit ended off. The cloud may report the requested state before the physical unit responds; the delay remains unmeasured.

Sol 6.1 Medium added the power test, including off cleanup after interruption and detection of changes to other controls. All 38 focused pump tests passed after the timing wording was clarified at `bae62fa`; offline demo/help, document parsing and whitespace checks also passed. Realtime updates, app-side change detection and the other two units remain untested.

## C2. PC service and web app against the live pumps (F2.3 check)

Extends [C](#c-mitsubishi-through-melcloud-home): the same cloud route, now through the F2.3 service and web app instead of the supervised probe. Runs on the home PC at the villa. Live mode reads the MELCloud Home credentials from the `VILLA_KUPPIS_MELCLOUD_USERNAME` and `VILLA_KUPPIS_MELCLOUD_PASSWORD` environment variables (development mode; F3.2 moves them to the Windows credential store). Each live test needs the owner's go-ahead.

Steps (owner present; go-ahead given before any write):

1. Agree on one unit, a setpoint change of at most 1 °C and an optional fan change. Keep power and mode unchanged. Record the original power, mode, setpoint and fan in private test notes. Have the official app available to restore them if needed.
2. Start `.venv\Scripts\python scripts/start-pump-app.py --live`. Enter credentials in the local dialog; email is visible and Show password lets the owner check typing. Credentials stay in process memory. This launcher creates a fresh temporary data directory and disables optional weather fetches for this check.
3. The browser opens at 127.0.0.1:8377. The direct environment-based `pump_app serve --live` command remains available for development; never put credentials in command arguments or files.
4. Open the page and confirm all three installed indoor units appear with an OK status (not stale), and that each card's power, mode, temperature and fan match the official mobile app at the same moment.
5. Soak for at least three poll cycles (10 seconds each): values stay fresh and the last-updated time advances.
6. Change one setpoint on one unit (owner chooses the unit and direction, within the reported range, for example +1 °C). The card shows the pending state, then a confirmed event row (read-back allows up to about 15 seconds). The event names the actor; the change is visible in the mobile app.
7. If separately authorized, change the fan of the same unit by one supported step; verify it the same way, then restore and verify the original fan. Do not change mode or power during this test.
8. Restore the setpoint to its original value and confirm the restoration in both apps. Confirm all four controls match the private baseline, including the unmodified mode and power. If any command fails or remains pending, inspect the official app before doing anything else and restore there if needed; do not blindly retry ambiguous writes.
9. Stop with Ctrl+C while nothing is pending.

Expected quirk: MELCloud returns HTTP 500 on writes that actually apply (recorded in C). The service then still confirms the command from the read-back, and the event row carries the note "the write response failed but the change was confirmed" under the status. That is a pass, not a failure.

Current naming: the web page does not yet ask for a name, so commands are logged with the actor `web`. Owner identification of changes arrives with F3.2.

Pass: steps 4–5 show all three units live and fresh; steps 6–8 are each confirmed by read-back, visible in the mobile app and in the event log; no other unit or control changed; the units end in their original states.

The outcome is recorded here with the commit run and the date.

The first supervised app launch on 19 October 2026 at `80bc6d4` stopped before connecting: aiohttp required a running event loop during client creation. Fixed at `4bfc692`: the service creates and closes that client on its gateway loop. All 121 pump tests passed, including a real aiohttp-session lifecycle check with cloud reads mocked; all 49 service tests passed again after the final startup-error guard. No device command was sent during the failed launch.

Owner-approved live acceptance passed on 19 October 2026 at `4bfc692`. All three units stayed fresh for more than three polling cycles, and the owner confirmed their initial controls matched MELCloud Home. The selected upstairs unit changed from 22 to 23 °C through the command API and from Auto fan to speed 1 through the web page. Both changes and both restorations produced confirmed events attributed to `web`; no write-response errors were reported in this session. The owner verified the changed state and final 22 °C/Auto restoration in MELCloud Home. Mode and power stayed unchanged, and all three units' final control values matched the saved baseline. The temporary service stopped with nothing pending; port 8377 was no longer listening. Credentials, unit identifiers, baseline and screenshots remain outside tracked project files. This verifies cloud settings, not physical fan or compressor operation while the unit is off.

Offline review on 19 October 2026: Codex reviewed the inherited service and unfinished history/weather batch. Fixes cover transactional and locked history access, command read-back recording, failed weather backfill retries, shutdown during a weather request, UTC/hour-aligned FMI queries, missing measurements, browser-origin/Host checks, and stale chart responses. The local credential launcher has mocked cancellation and cleanup checks. A synthetic year with roughly 140,000 pump readings plus hourly outdoor and quarter-hour prices returned 1,000 points per series (148 KB) in 0.13 seconds on the owner's PC and rendered in the browser. This is offline evidence, not a live-device pass. The app's stop command does not reverse accepted device commands.

## C3. Windows startup and Tailscale phone access (F3.2 check)

The unattended app stores data under the owner's local Windows profile. Its configuration and task XML contain paths and the private Tailscale origin, but no passwords. Keep them outside Git. The backup folder is selected during setup; repository-local `db/` is ignored. A backup on the same disk does not protect against losing that disk.

Preparation and installation:

1. Run `.venv\Scripts\python scripts/setup-pump-service.py plan --backup-dir BACKUP_DIR`. This reads Tailscale/task status only. Review the proposed paths, HTTPS origin and Windows account task. Ensure no temporary app still owns port 8377.
2. With the owner present, run the same command with `apply`. The owner enters MELCloud credentials in the local dialog and chooses **Save and enable service**. That action enrolls the credential for this Windows account and authorizes the live service connection. Cancellation installs nothing. No device command is submitted during setup.
3. Complete any Tailscale HTTPS consent on the vendor's page. Approve Windows elevation for task registration, then enter the Windows account password in the native `schtasks` prompt. The Windows Hello PIN cannot replace that password. The app task itself runs unelevated, with Password logon and a boot trigger. Do not enable Windows autologin.
4. Run `.venv\Scripts\python scripts/setup-pump-service.py inspect`; check the app's private HTTPS origin and localhost page. The service log is rotated in its data directory. Check for a dated backup directory and completed SQLite snapshots. No credentials should appear in config, task arguments or logs.

Phone and boot acceptance (owner present):

1. The phone needs Tailscale signed in to the same network and a normal browser. No APK installation is needed for this web-app check. Turn Wi-Fi off, keep Tailscale connected, and open the private HTTPS origin printed by setup.
2. Confirm fresh units and the signed-in user's identity. For an explicitly approved device check, record one unit's baseline, change its target by at most 1 °C, then restore it. Verify both confirmed events contain the Tailscale identity and match MELCloud Home. Leave mode, power, fan and other units unchanged. Do not retry an ambiguous write without inspection.
3. When convenient for the owner, reboot the PC. Before signing in to Windows, use the phone on mobile data to confirm the app resumes and gets fresh pump readings. Check task state and logs after login. Do not claim before-login startup from a manual task launch alone.

The background Serve configuration persists separately from Tailscale's unattended setting. The setup uses Serve for private access and never enables Funnel. [Tailscale documents identity headers and the localhost trust boundary](https://tailscale.com/docs/features/tailscale-serve), and [unattended mode](https://tailscale.com/docs/how-to/run-unattended). [Windows documents the credential's account scope](https://learn.microsoft.com/en-us/windows/win32/api/wincred/nf-wincred-credreadw) and [password prompting in schtasks](https://learn.microsoft.com/en-us/windows/win32/taskschd/schtasks). Windows task termination or power loss may skip Python cleanup; SQLite transactions and orphan-command handling cover restart recovery.

Current acceptance: pending installation, phone and before-login boot checks. Offline results and the deployed commit will be recorded here.

## D. Spot prices and fixed thresholds

No hardware needed. Fetch tomorrow's 15-minute FI spot prices from Elering (no key), store them, and compute plans from fixed thresholds per room and per site, for heating and cooling. Test against saved price fixtures with the network mocked.

The Python experiment lives in `villa_kuppis/`; [README](../README.md#development) has the offline demo and test commands. Its saved fixtures are synthetic Elering-format responses for a normal day and both 2026 daylight-saving changes. They are test data, not historical market prices or evidence of a live fetch.

The [Elering API specification](https://dashboard.elering.ee/v3/api-docs) defines `start` and `end` as date-time query strings. Returned interval timestamps are Unix seconds. The experiment stores raw €/MWh prices, converts them to c/kWh for planning, and rejects incomplete days before replacing saved prices.

This step computes temperature targets. Cooling shutoff, measured-temperature checks, minimum off time and device commands remain in [F4.1](tasks/0.2.0.md#f6-pump-automation).

Validated on 19 October 2026 at `29cd774`: 102 tests passed in the Windows Python 3.13 project environment. The offline demo ran in a fresh temporary data directory, and help, document parsing and whitespace checks passed. Tests cover both DST days, failed fetches and database writes preserving saved prices, room overrides, strict thresholds, daily scheduling across restarts, and CLI validation before network access. Network boundaries were mocked in these tests.

Owner-approved live check on 19 October 2026, using the implementation at `62dedd0`: one Elering fetch stored all 96 FI intervals for that Helsinki day in a new temporary SQLite database. A separate database read confirmed 00:00 through 23:45 at 900-second spacing without another fetch. This validates on-demand retrieval and persistence; the unattended daily scheduler has only mocked test coverage.

## A. Cottage through the Home APIs

Prerequisite: heaters and sensors commissioned into Google Home over Matter.

1. Build [Google's sample](https://github.com/google-home/google-home-api-sample-app-android) at a pinned revision with the `home.platform.selected.devices` scope.
2. List the cottage devices and their traits. Read each ALPSTUGA (temperature, CO2, PM2.5, humidity, as exposed) and each heater (temperature, setpoint, mode).
3. Over mobile data, change one heater's setpoint, confirm the read-back, restore it.
4. Run a background job that reads all devices every 15 minutes for a day. Record whether the Home APIs work in the background at all, and how often Android actually runs the job.
5. Check whether the heaters expose power or energy over Matter.

Pass: steps 2–3 work over mobile data. Step 4 shows how complete the cottage graphs will be.

## B. Sensor-driven automations

1. Give the cottage home a street address in Google Home (needed for time-based starters).
2. From the app, create the small room's automation: every 30 minutes, read the ALPSTUGA; below target minus hysteresis, set the heater to target + margin; above target plus hysteresis, set it to target − margin.
3. Confirm it sets the heater's absolute setpoint and runs with the phone off.
4. Encode a day of 12 time windows with different targets in both rooms' automations. Confirm it fits Google's limits and follows the windows.
5. Replace both automations from a background job, as the daily upload will.
6. Try one automation with separate branches for both rooms, every 15 minutes.

Pass: a day's plan runs from Google Home with the phone and PC off. If it fails, use the time-based fallback in [platform decision](platform-decision.md#sensor-driven-automations).

## Next step

| Evidence | Next |
| --- | --- |
| C and D pass | Build the PC service and the home side of the web app |
| A and B pass | Build the Android app's cottage functions and the daily upload |
| B fails | Use the time-based fallback in [platform decision](platform-decision.md#sensor-driven-automations) |
