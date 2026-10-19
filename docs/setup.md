# Setup record

- Created 17 October 2026 from the owner's project template with a fresh Git history; no template history, machine configuration or credentials included.
- Remote: [Vkuparin/villa-kuppis](https://github.com/Vkuparin/villa-kuppis), public, created by the owner with Apache License 2.0 in the initial commit `866048c`. Scaffold published as `c3124bf`.
- Coordinator, dashboard and agent launcher scripts come from the template. Their Windows paths (`D:/AIstuff/...`) are the owner's machine conventions, not dependencies of the app.
- `node scripts/check-docs.mjs` needs the shared dev-dashboard and only runs on the owner's machine. In cloud sessions, check local Markdown links and `git diff --check` instead.
- Implementation began on 19 October 2026 with the spot-price experiment. No live device access or paid service has been tested.

## Development tools (card F1.11)

Python 3.13.3 is already installed and the project has an isolated `.venv`. The `python` command on PATH selects an older 3.10 installation, so use `py -3.13` to create the environment and its own Python for project commands. Android Studio, the SDK and Tailscale are still pending. Install missing system tools with the owner's go-ahead, preferably with `winget`:

| Tool | For |
| --- | --- |
| Python 3.13 with a per-project virtual environment | PC service and tests; existing installation verified |
| Android Studio, including the Android SDK, platform tools and its bundled JDK | Android app |
| Tailscale | Phone access to the PC |
| Git and Node.js | Already used by the project scripts |

A physical Android phone with USB debugging is the simplest test device; Home APIs need Google Play services and the owner's Google Home account. The app is a self-signed APK installed directly; no Play Store account or paid signing. After installing, record the run and test commands in AGENTS.md.
