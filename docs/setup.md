# Setup record

- Created 17 October 2026 from the owner's project template with a fresh Git history; no template history, machine configuration or credentials included.
- Remote: [Vkuparin/villa-kuppis](https://github.com/Vkuparin/villa-kuppis), public, created by the owner with Apache License 2.0 in the initial commit `866048c`. Scaffold published as `c3124bf`.
- Coordinator, dashboard and agent launcher scripts come from the template. Their Windows paths (`D:/AIstuff/...`) are the owner's machine conventions, not dependencies of the app.
- `node scripts/check-docs.mjs` needs the shared dev-dashboard and only runs on the owner's machine. In cloud sessions, check local Markdown links and `git diff --check` instead.
- Implementation began on 19 October 2026 with the spot-price experiment. No live device access or paid service has been tested.

## Development tools (card F1.11)

The owner authorized development software installation on 19 October 2026. Python 3.13.3 was already installed; the project now has an isolated `.venv`. The `python` command on PATH selects an older 3.10 installation, so use `py -3.13` to create the environment and its own Python for project commands.

| Tool | Installed and verified |
| --- | --- |
| Python | 3.13.3; project `.venv` created |
| Android Studio | Rabbit 1, 2026.2.1.8; installer checksum and installed product metadata verified |
| Bundled JDK | OpenJDK 25.0.3; `java -version` passed |
| Android SDK | Command-line tools 22.0, platform 36, build tools 36.0.0, platform tools 37.0.1; package listing, `adb version` and `aapt2 version` passed |
| Tailscale | 1.104.1; version command passed, Windows service running |
| Git and Node.js | Already used by the project scripts |

Android Studio and Tailscale were installed from their official distributions. The Studio mirror used by `winget` stalled, so the same installer was downloaded directly from Google and checked against the published SHA-256. SDK packages came from Google's SDK manager. Installation references: [Android Studio](https://developer.android.com/studio), [SDK manager](https://developer.android.com/tools/sdkmanager), [Tailscale for Windows](https://tailscale.com/docs/install/windows).

The SDK lives outside the repository. User-level `ANDROID_HOME` points to it; `JAVA_HOME` points to Studio's bundled JDK. The SDK command-line tools, platform tools, JDK and Tailscale directories are on the user's PATH. Restart Codex and open a new terminal to pick up these environment changes.

Verify tools in that terminal:

```powershell
py -3.13 --version
java -version
adb version
sdkmanager --sdk_root="$env:ANDROID_HOME" --list_installed
tailscale version
```

A physical Android phone with USB debugging is the simplest test device; Home APIs need Google Play services and the owner's Google Home account. The app is a self-signed APK installed directly; no Play Store account or paid signing. Account sign-in, device pairing and live integration checks remain separate from this tool setup.
