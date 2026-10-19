"""Plan or install the current user's unattended Villa Kuppis app."""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from villa_kuppis.windows_credentials import Credentials, write_credentials
from villa_kuppis.windows_setup import (
    TASK_NAME, TASK_DESCRIPTION, public_origin, serve_matches_or_empty,
    task_xml, task_matches, write_new_or_identical,
)


def command(args, *, check=True):
    result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", creationflags=subprocess.CREATE_NO_WINDOW)
    if check and result.returncode:
        raise RuntimeError(f"{Path(args[0]).name} failed (exit {result.returncode})")
    return result


def tailscale_executable():
    import shutil
    return shutil.which("tailscale") or str(Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Tailscale/tailscale.exe")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "apply", "inspect"))
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args(argv)
    if os.name != "nt":
        parser.error("Windows is required")
    home = Path(os.environ["LOCALAPPDATA"]) / "VillaKuppis"
    config_path = home / "service.json"
    ts = tailscale_executable()
    status = json.loads(command([ts, "status", "--json"]).stdout)
    serve = json.loads(command([ts, "serve", "status", "--json"]).stdout or "{}")
    origin = public_origin(status)
    query = command(["schtasks.exe", "/Query", "/TN", TASK_NAME, "/XML"], check=False)
    task_exists = query.returncode == 0
    if args.action == "inspect":
        print(json.dumps({"tailscale_connected": True, "origin": origin,
                          "config_present": config_path.exists(), "task_present": task_exists,
                          "serve": serve_matches_or_empty(serve, origin, 8377)}))
        return 0
    backup = (args.backup_dir or (home / "backups")).resolve()
    data = home / "data"
    if data.resolve() == backup:
        raise ValueError("Data and backup folders must be different")
    config_text = json.dumps({"data_dir": str(data), "backup_dir": str(backup),
                              "public_origin": origin, "port": 8377}, indent=2) + "\n"
    if config_path.exists() and config_path.read_text(encoding="utf-8-sig") != config_text:
        raise ValueError("Existing service configuration differs; leaving it unchanged")
    state = serve_matches_or_empty(serve, origin, 8377)
    # No replacement of an existing task, even if its name looks familiar.
    if task_exists:
        owner_info = next(csv.reader(io.StringIO(command(["whoami.exe", "/user", "/fo", "csv", "/nh"]).stdout)))
        expected_task = task_xml(sys.executable, ROOT, config_path, owner_info[1])
        if task_matches(query.stdout, expected_task) and config_path.exists() and state == "matching":
            print("Existing managed setup found; nothing changed. Use inspect or Windows Task Scheduler to start it.")
            return 0
        raise ValueError("An existing VillaKuppis task needs review; it was not replaced")
    print(f"App: {origin}\nData: {data}\nBackups: {backup}")
    print("Boot task: current Windows account, password logon, least privilege.")
    print("Tailscale: unattended mode and private HTTPS Serve. No Funnel.")
    if args.action == "plan":
        print("Plan only: no files, credentials, tasks or network settings changed.")
        return 0
    # Enrollment runs unelevated as the same user who will own the boot task.
    prompt = runpy.run_path(str(ROOT / "scripts/start-pump-app.py"))["gui_getpass"](
        title="Villa Kuppis - save MELCloud credentials",
        notice="Save in Windows Credential Manager for automatic startup. The task will connect to MELCloud and enable controls through your private Tailscale network.",
        accept_label="Save and enable service")
    username, password = prompt("Email"), prompt("Password")
    if not username or not password:
        print("Cancelled; nothing was installed.")
        return 0
    user = command(["whoami.exe"]).stdout.strip()
    sid_row = next(csv.reader(io.StringIO(command(["whoami.exe", "/user", "/fo", "csv", "/nh"]).stdout)))
    definition = task_xml(sys.executable, ROOT, config_path, sid_row[1])
    definition_path = home / "startup-task.xml"
    # Capture immutable setup files before altering Windows/Tailscale settings.
    write_new_or_identical(config_path, config_text)
    write_new_or_identical(definition_path, definition)
    write_credentials(Credentials(username, password))
    # The dialog can remain open while another app changes Serve configuration.
    state = serve_matches_or_empty(json.loads(command(
        [ts, "serve", "status", "--json"]).stdout or "{}"), origin, 8377)
    command([ts, "set", "--unattended=true"])
    if state == "empty":
        serve_result = subprocess.run([ts, "serve", "--bg", "--https=443", "http://127.0.0.1:8377"])
        if serve_result.returncode:
            # Vendor may print an HTTPS-enablement consent link. Show locally only.
            raise RuntimeError("Tailscale Serve needs local consent; finish it and run apply again")
    updated = json.loads(command([ts, "serve", "status", "--json"]).stdout)
    if serve_matches_or_empty(updated, origin, 8377) != "matching":
        raise RuntimeError("Tailscale Serve was not configured; startup task was not registered")
    helper = ROOT / "scripts/register-pump-task.ps1"
    # No secret enters this command. Windows' schtasks prompt owns the password.
    ps_args = ["-NoProfile", "-ExecutionPolicy", "RemoteSigned", "-File", str(helper),
               "-TaskFile", str(definition_path), "-TaskUser", user]
    argument_string = subprocess.list2cmdline(ps_args)
    import ctypes
    from ctypes import wintypes
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    shell.ShellExecuteW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                   wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int]
    shell.ShellExecuteW.restype = ctypes.c_void_p
    result = shell.ShellExecuteW(None, "runas", "powershell.exe", argument_string, str(ROOT), 1)
    if not result or result <= 32:
        raise RuntimeError("Windows did not open task registration; configuration and enrolled credential remain for retry")
    print("Complete the local administrator prompt and Windows task password. No reboot is started.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Setup stopped: {error}", file=sys.stderr)
        raise SystemExit(1)
