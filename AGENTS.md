# Project

Project: Villa Kuppis.

Purpose: A personal Finnish-language home automation app that brings the home and cottage devices into one app.

Stack: Implementation started. Python 3.13 with SQLite for the PC service; tzdata supplies Europe/Helsinki time on Windows. The pump experiment uses aiomelcloudhome 0.3.1. Planned: Finnish web app for desktop, native Kotlin Android app (Google Home APIs, web app in a WebView) for the cottage devices in Google Home over Matter, and Tailscale between phones and PC. No Home Assistant or VM unless last resort. Update this line when the runtime, framework or key libraries change.

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
2. **Route.** Kynsi coordinates and implements Tier 2–3 when assigned that role. Scripts do mechanical work. For Kynsi-led coordination, send each completed Tier 2 batch, with its diff and focused check evidence, to Sol 6.1 Light (`gpt-6.1-sol`, `low`) for review before integrating it. Sol 6.1 Medium (`gpt-6.1-sol`, `medium`) handles Tier 1 design, diagnosis, ambiguous behavior and shared interfaces. Kynsi may call paid Codex agents as needed within approved project scope without per-call permission; use ChatGPT-authenticated Codex CLI and subscription usage limits, never API credits. Give one concrete local repair for review findings, then use Light to finish bounded unresolved work or Medium if it needs Tier 1 judgment. Keep paid agents sequential unless the owner asks for parallel work. Hermes must never invoke Claude. When the owner starts Claude as coordinator, retain its existing workflow: Claude coordinates and handles Tier 1, delegates Tier 3 and familiar Tier 2 to Kynsi, reviews the result, gives one concrete repair and finishes unresolved work itself. See docs/agents.md for delegation and runtime rules.
3. **Stay resumable.** A session can end without warning. Write the card's `In progress` line when you start and commit after every passing step; WIP commits are fine. The next coordinator resumes from `status`, the card and Git.
4. **Finish.** Fetch, commit and push to the project remote; this is authorized. The owner explicitly requested a public repository. Publish only reviewed project files; never publish private template history, credentials, captures or personal deployment configuration. Do not change visibility without an explicit owner request. Mark the card `Done` with the commit and checks run, then release the lease.

Kynsi is the same persistent Hermes agent reached through `hermes.agent_connection`, `agent_request` and `agent_request_status`. Never spawn stateless local workers, separate Hermes profiles or local subagents, or call its model directly. Use Hermes's selected local model and respect `local_runtime.enabled`; when false, leave it off and use the calling harness's suitable paid model within scope. Explicit local-only tasks wait. A real assignment may restore an existing stopped gateway and wake the selected downloaded model while enabled. Never restart an active gateway. Inspect partial work before paid continuation; do not replay uncertain requests.

Details: [docs/agents.md](docs/agents.md). Goals and decisions: [docs/roadmap.md](docs/roadmap.md). Cards: [docs/tasks/](docs/tasks/README.md).
