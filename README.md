# Villa Kuppis

A personal Finnish-language home automation app that brings the home and cottage devices into one app.

**Status: research and design scaffold. No working app or device integration yet.** The UI will be Finnish. Start with the [design documents](docs/README.md), the [cottage plan](docs/cottage.md) and [spot-price automation](docs/spot-price.md).

Repository: [Vkuparin/villa-kuppis](https://github.com/Vkuparin/villa-kuppis). The project is based on the owner's template and contains no private template history. See the [setup record](docs/setup.md) for verification and environment limitations.

## Start here

Open Codex or Claude Code from its app with this project's directory, or ask persistent Kynsi to coordinate it:

```powershell
node scripts/start-agent.mjs hermes
```

All three read [AGENTS.md](AGENTS.md). Whichever one you start coordinates. All local agent work goes to Kynsi, with the same memory and conversation you use from Telegram and Desktop. It uses Hermes's selected local model while `local_runtime.enabled` is true. When the switch is off, Codex/Claude uses a suitable paid model without waking the local runtime. [Agent workflow](docs/agents.md) covers routing, Kynsi and resuming. To see where things stand:

```powershell
node scripts/coordinate.mjs status
```

## Development

Release goals and product decisions live in [docs/roadmap.md](docs/roadmap.md). Implementation cards live in [docs/tasks/](docs/tasks/README.md). Add product-specific setup and build commands here as code lands.

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
| `scripts/coordinate.mjs` | Coordinator lease and the status a new session resumes from |
| `scripts/start-agent.mjs` | Ask existing Kynsi to coordinate this project |
| `scripts/dashboard.mjs`, `scripts/check-docs.mjs` | Shared dashboard launch and document validation |
| `.claude/settings.json` | Shared Claude permission defaults |
| `scratch/` | Ignored private reference material |

Personal harness settings and model choices stay outside the project. `.claude/settings.local.json` is ignored.

Licensed under the [Apache License 2.0](LICENSE), selected by the owner when creating the repository.
