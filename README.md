# Villa Kuppis

A personal Finnish-language home automation app that brings the home and cottage devices into one app.

**Status: the PC pump service and Finnish web app are ready for a supervised device check.** The app includes controls, an event log and pump, outdoor-temperature and spot-price history. Android and automation are still pending. Start with the [design documents](docs/README.md), the [cottage plan](docs/cottage.md) and [spot-price automation](docs/spot-price.md).

Repository: [Vkuparin/villa-kuppis](https://github.com/Vkuparin/villa-kuppis). The project is based on the owner's template and contains no private template history. See the [setup record](docs/setup.md) for verification and environment limitations.

## Start here

Open Codex or Claude Code from its app with this project's directory, or ask persistent Kynsi to coordinate it:

```powershell
node scripts/start-agent.mjs hermes
```

All three read [AGENTS.md](AGENTS.md). In the Kynsi-led workflow, persistent Kynsi coordinates and implements T2–T3; Sol 6.1 Light reviews completed T2 batches and Medium handles T1. Kynsi can call Codex CLI as needed using ChatGPT subscription limits, with no API billing or Claude invocation. Owner-assigned Claude coordination stays unchanged. The local runtime switch remains authoritative. [Agent workflow](docs/agents.md) covers routing and resuming. To see where things stand:

```powershell
node scripts/coordinate.mjs status
```

## Development

Release goals and product decisions live in [docs/roadmap.md](docs/roadmap.md). Implementation cards live in [docs/tasks/](docs/tasks/README.md).

The first implementation is a command-line spot-price experiment. It stores complete Finnish price days in SQLite and computes temperature targets from fixed thresholds. The offline demo uses synthetic prices and room settings:

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
$devData = Join-Path ([IO.Path]::GetTempPath()) ("villa-kuppis-dev-" + [guid]::NewGuid())
.venv\Scripts\python -m villa_kuppis --data-dir "$devData" demo
.venv\Scripts\python -m unittest discover -s tests -v
```

All data commands require an explicit data directory. `--help` lists the on-demand price fetch and opt-in daily scheduler. See the [setup record](docs/setup.md) for installed development tools.

Start the offline pump app with `.venv\Scripts\python scripts/start-pump-app.py --demo`. It opens the browser and uses fresh temporary data. After the owner authorizes the specific live test, use `--live`: a local dialog asks for email and password, includes Show password, and keeps credentials in memory. Close the service with Ctrl+C after restoring the tested controls. Stopping the app does not restore device settings automatically.

The app listens on localhost and rejects unrelated browser origins. The unattended setup uses Windows Credential Manager, a password-backed boot task, and private Tailscale HTTPS access. Run `.venv\Scripts\python scripts/setup-pump-service.py plan --backup-dir BACKUP_DIR` to inspect the proposed setup without changes; `apply` opens local credential enrollment and task registration. See [Windows setup and phone testing](docs/integration-experiments.md#c3-windows-startup-and-tailscale-phone-access-f32-check). Deployment and boot acceptance must be verified separately from offline tests.

Optional outdoor data for the direct `pump_app serve --live` command uses `VILLA_KUPPIS_WEATHER_HOME` and `VILLA_KUPPIS_WEATHER_COTTAGE` (`lat,lon`); keep precise locations in private environment settings. The supervised launcher leaves weather disabled; the unattended runner fetches it when its configuration file has an optional `"weather": {"home": "lat,lon"}` entry. With weather on, the service also stores FMI's 48-hour point forecast. FMI observations use the nearest returned station, not a sensor at the property. Prices appear when the price experiment has populated `prices.db` in the same data directory. Missing history is shown as a gap; energy, cottage and plan series need their later integrations.

The [Mitsubishi probe](docs/mitsubishi.md#probe-commands) prepares the next integration test with a synthetic demo and a supervised setpoint change. Live account and device checks require approval; credentials are entered locally and are not saved.

Launch the shared dashboard against this checkout:

```powershell
node scripts/dashboard.mjs --open
```

You can also double-click the **Dev dashboard** desktop shortcut or the shared installation's `Start dashboard.cmd`, then choose this checkout.

Validate its source documents:

```powershell
node scripts/check-docs.mjs
```

Both commands use the machine configuration at `D:/AIstuff/agent-workflow/config.json`, or `AGENT_WORKFLOW_CONFIG`. The shared path avoids Windows Store apps seeing different AppData folders. The default dashboard installation is `D:/AIstuff/repos/dev-dashboard`; an unavailable parser makes validation fail clearly.

## Project layout

| Path | Purpose |
| --- | --- |
| `AGENTS.md`, `CLAUDE.md` | Shared instructions and Claude's import |
| `docs/agents.md` | Routing, persistent Kynsi, resuming and machine tools |
| `docs/roadmap.md`, `docs/tasks/` | Goals, decisions and canonical task cards |
| `villa_kuppis/`, `tests/` | Price experiment and offline tests with synthetic fixtures |
| `scripts/coordinate.mjs` | Coordinator lease and the status a new session resumes from |
| `scripts/start-agent.mjs` | Ask existing Kynsi to coordinate this project |
| `scripts/dashboard.mjs`, `scripts/check-docs.mjs` | Shared dashboard launch and document validation |
| `.claude/settings.json` | Shared Claude permission defaults |
| `scratch/` | Ignored private reference material |

Personal harness settings and model choices stay outside the project. `.claude/settings.local.json` is ignored.

Licensed under the [Apache License 2.0](LICENSE), selected by the owner when creating the repository.
