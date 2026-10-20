# Project

Project: Villa Kuppis.

Purpose: A personal Finnish-language home automation app that brings the home and cottage devices into one app.

Stack: Python 3.13 and SQLite for the PC service, a Finnish web app served by the standard-library HTTP server, aiomelcloudhome 0.3.1 for pumps, and tzdata for Europe/Helsinki on Windows. FMI observations use the standard-library HTTP/XML client. Planned (decided 2026-10-20): one Home Assistant per site as device gateway, on two always-on machines: home HP OmniBook laptop with Debian + Docker Compose with Villa Kuppis and Home Assistant Container (MELCloud Home), cottage HP MP9 G4 mini-PC with Home Assistant OS and the Matter Server; Tailscale between browsers, the home server and the cottage Home Assistant; browser/PWA only, no Android app. Update this line when the runtime, framework or key libraries change.

Codex, Hermes and Claude Code (through `CLAUDE.md`) all read this file. This is a hobby project: move quickly, keep process small, and ask the owner only about real product choices.

## Everyone

- Work in development mode with an isolated temporary data directory. Never use the owner's live accounts, personal data or paid AI allowance for testing; a live test needs the owner's explicit go-ahead.
- Test what you change: run the affected tests, add one when new behavior has none, and mock external network boundaries. Focus on core use cases and likely failures. Avoid speculative edge-case work and extra process; skip full-suite runs and extra documents unless asked.
- Report only checks you actually ran. Write plainly; apply the unslop skill when available.
- Keep private reference material in ignored `scratch/`. Never commit credentials or personal data.
- Never reset, stash or overwrite work you did not make. Stage only your own changes.

| Purpose | Command |
| --- | --- |
| Create the project environment | py -3.13 -m venv .venv; .venv\Scripts\python -m pip install -r requirements.txt |
| Run the spot-price experiment | .venv\Scripts\python -m villa_kuppis --help (development commands require an explicit temporary data directory) |
| Offline demo | $devData = Join-Path ([IO.Path]::GetTempPath()) ("villa-kuppis-dev-" + [guid]::NewGuid()); .venv\Scripts\python -m villa_kuppis --data-dir "$devData" demo |
| Python tests (mocked network, temporary data) | .venv\Scripts\python -m unittest discover -s tests -v |
| Pump probe help and offline demo | .venv\Scripts\python -m villa_kuppis.pump_probe --help; .venv\Scripts\python -m villa_kuppis.pump_probe demo |
| Pump probe tests (mocked network and devices) | .venv\Scripts\python -m unittest discover -s tests -p "test_pump*.py" -v |
| FMI weather tests (synthetic responses and local HTTP fixtures) | .venv\Scripts\python -m unittest discover -s tests -p "test_weather.py" -v |
| Learned room rates tests (synthetic rooms, fake forecast, localhost HTTP) | .venv\Scripts\python -m unittest discover -s tests -p "test_learning.py" -v |
| Cooling optimizer tests (recorded price days, synthetic room, mock devices) | .venv\Scripts\python -m unittest discover -s tests -p "test_optimizer.py" -v |
| Home Assistant gateway tests (fake HA WebSocket server on localhost) | .venv\Scripts\python -m unittest discover -s tests -p "test_ha_gateway.py" -v |
| Cottage controller simulations (no Home Assistant) | .venv\Scripts\python -m unittest discover -s tests -p "test_cottage_control.py" -v |
| Cottage integration in Home Assistant core (separate venv: pip install -r tests_ha/requirements.txt; Linux/WSL) | python -m pytest -c tests_ha/pytest.ini tests_ha |
| Pump app tests (mock devices and localhost HTTP) | .venv\Scripts\python -m unittest discover -s tests -p "test_pump_app.py" -v |
| Cottage readings, history and events tests (fake Home Assistant on localhost, injected clocks) | .venv\Scripts\python -m unittest discover -s tests -p "test_cottage_view.py" -v |
| Bind cottage devices (needs `sites` in the config; discover reads the cottage Home Assistant, owner approval for live) | .venv\Scripts\python -m villa_kuppis.sites --config CONFIG_FILE discover; .venv\Scripts\python -m villa_kuppis.sites --config CONFIG_FILE bind cottage DEVICE_ID ENTITY_ID KIND --room large; .venv\Scripts\python -m villa_kuppis.sites --config CONFIG_FILE list |
| Startup, credential and backup tests (mock Windows/network boundaries; temporary data) | .venv\Scripts\python -m unittest discover -s tests -p "test_windows_runtime.py" -v; .venv\Scripts\python -m unittest discover -s tests -p "test_backup.py" -v |
| Credential file tests (temporary data) | .venv\Scripts\python -m unittest discover -s tests -p "test_credential_file.py" -v |
| Home server image, demo only (Linux/Docker; temporary folders, see deploy/home/README.md "Try it offline") | docker compose -f deploy/home/compose.yaml -f deploy/home/compose.demo.yaml config; docker compose -f deploy/home/compose.yaml -f deploy/home/compose.demo.yaml up -d --build |
| Check or restore a backup day (stop the service first for restore) | .venv\Scripts\python -m villa_kuppis.backup verify BACKUP_DAY_DIR; .venv\Scripts\python -m villa_kuppis.backup restore BACKUP_DAY_DIR DATA_DIR |
| Update the home server (Debian; touches only villa-kuppis) | scripts/update-home-server.sh |
| Plan unattended setup (no changes) | .venv\Scripts\python scripts/setup-pump-service.py plan --backup-dir BACKUP_DIR |
| Configure unattended app (local credential enrollment and Windows task password; owner present) | .venv\Scripts\python scripts/setup-pump-service.py apply --backup-dir BACKUP_DIR |
| Inspect unattended app (no credential values) | .venv\Scripts\python scripts/setup-pump-service.py inspect |
| Run configured service (live requires owner go-ahead; demo uses explicit temporary paths) | .venv\Scripts\python -m villa_kuppis.runtime --config CONFIG_FILE [--demo] |
| Inspect Tailscale connection and publication | tailscale status --json; tailscale serve status --json |
| Supervised pump web app (fresh temporary data; local login dialog with Show password) | .venv\Scripts\python scripts/start-pump-app.py --demo; .venv\Scripts\python scripts/start-pump-app.py --live (owner approval) |
| Live pump probe (owner approval; local interactive terminal) | .venv\Scripts\python -m villa_kuppis.pump_probe --live read; .venv\Scripts\python -m villa_kuppis.pump_probe --live test-setpoint |
| Live pump power test (owner approval; starts off, on for 30 seconds after read-back, then off) | .venv\Scripts\python -m villa_kuppis.pump_probe --live test-power |
| One-off live price fetch (owner approval; new temporary data directory) | .venv\Scripts\python -m villa_kuppis --data-dir DATA_DIR refresh --date YYYY-MM-DD |
| Development tool verification (new terminal after installation) | py -3.13 --version; java -version; adb version; sdkmanager --sdk_root="$env:ANDROID_HOME" --list_installed; tailscale version |
| Type, build and format checks | node scripts/check-docs.mjs (requires shared dev-dashboard); git diff --check |

Define a command here before first relying on it. Add rows when the project needs them.

## Workers

An assignment with owned files wins over the rest of this file. Edit only those files and run the named checks. Do not commit, push or edit task cards. Return what changed, the checks you ran and what is unresolved.

## Coordinators

You coordinate when the owner starts you in this checkout. There is one coordinator at a time.

1. **Start.** Run `node scripts/coordinate.mjs status` for the lease, branch, uncommitted files, recent commits, worktrees and cards. Then run `node scripts/coordinate.mjs takeover --owner <harness>:<thread>`. The owner starting you authorizes replacing a lease left behind; ask only if the owner said two coordinators are running.
2. **Route.** By default, the Codex coordinator handles Tier 1 and Tier 2 design, diagnosis, implementation, review and integration. Delegate only clear, bounded Tier 3 work to Sol 6.1 Light (`gpt-6.1-sol`, `low`). Scripts do mechanical work. Kynsi is available only when the owner explicitly opts in; never route Codex work to Kynsi automatically or as a fallback. Keep paid agents sequential unless the owner asks for parallel work. Use ChatGPT-authenticated Codex and subscription usage limits, never API credits. Hermes must never invoke Claude. When the owner starts Claude as coordinator, retain its existing workflow: Claude coordinates and handles Tier 1, delegates Tier 3 and familiar Tier 2 to Kynsi, reviews the result, gives one concrete repair and finishes unresolved work itself. See docs/agents.md for delegation and runtime rules.
3. **Stay resumable.** A session can end without warning. Write the card's `In progress` line when you start and commit after every passing step; WIP commits are fine. The next coordinator resumes from `status`, the card and Git.
4. **Finish.** Fetch, commit and push to the project remote; this is authorized. The owner explicitly requested a public repository. Publish only reviewed project files; never publish private template history, credentials, captures or personal deployment configuration. Do not change visibility without an explicit owner request. Mark the card `Done` with the commit and checks run, then release the lease.

For owner-approved Kynsi work, Kynsi is the same persistent Hermes agent reached through `hermes.agent_connection`, `agent_request` and `agent_request_status`. Never spawn stateless local workers, separate Hermes profiles or local subagents, or call its model directly. Use Hermes's selected local model and respect `local_runtime.enabled`; when false, leave it off and use the calling harness's suitable paid model within scope. Explicit local-only tasks wait. A real assignment may restore an existing stopped gateway and wake the selected downloaded model while enabled. Never restart an active gateway. Inspect partial work before paid continuation; do not replay uncertain requests.

Details: [docs/agents.md](docs/agents.md). Goals and decisions: [docs/roadmap.md](docs/roadmap.md). Cards: [docs/tasks/](docs/tasks/README.md).
