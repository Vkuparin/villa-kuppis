"""Inspectable, nonsecret Windows startup and Tailscale setup plans."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

TASK_NAME = "VillaKuppis"
TASK_DESCRIPTION = "Villa Kuppis localhost pump app (managed setup v1)"
NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"


def task_xml(python, root, config, user):
    for path in (python, root, config):
        if not Path(path).is_absolute():
            raise ValueError("Task paths must be absolute")
    ET.register_namespace("", NS)
    task = ET.Element(f"{{{NS}}}Task", version="1.2")
    def child(parent, tag, text=None, **attrs):
        el = ET.SubElement(parent, tag, **attrs)
        if text is not None:
            el.text = str(text)
        return el
    registration = child(task, "RegistrationInfo")
    child(registration, "Description", TASK_DESCRIPTION)
    trigger = child(child(task, "Triggers"), "BootTrigger")
    child(trigger, "Enabled", "true")
    child(trigger, "Delay", "PT30S")
    principal = child(child(task, "Principals"), "Principal", id="Owner")
    child(principal, "UserId", user)
    child(principal, "LogonType", "Password")
    child(principal, "RunLevel", "LeastPrivilege")
    settings = child(task, "Settings")
    for key, value in {
        "MultipleInstancesPolicy": "IgnoreNew", "DisallowStartIfOnBatteries": "false",
        "StopIfGoingOnBatteries": "false", "StartWhenAvailable": "true",
        "RunOnlyIfNetworkAvailable": "false", "Enabled": "true",
        "ExecutionTimeLimit": "PT0S",
    }.items():
        child(settings, key, value)
    restart = child(settings, "RestartOnFailure")
    child(restart, "Interval", "PT1M")
    child(restart, "Count", "3")
    action = child(child(task, "Actions", Context="Owner"), "Exec")
    child(action, "Command", str(python))
    child(action, "Arguments", subprocess.list2cmdline(
        ["-m", "villa_kuppis.runtime", "--config", str(config)]))
    child(action, "WorkingDirectory", str(root))
    # No encoding declaration: valid both as UTF-8 on disk and as a COM BSTR.
    return ET.tostring(task, encoding="unicode")


def public_origin(status):
    if status.get("BackendState") != "Running" or not status.get("Self", {}).get("Online"):
        raise ValueError("Sign in to Tailscale on this PC first")
    dns = status["Self"].get("DNSName", "").rstrip(".").lower()
    if not dns.endswith(".ts.net") or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-." for c in dns):
        raise ValueError("Tailscale MagicDNS name is unavailable")
    return "https://" + dns


def serve_matches_or_empty(raw, origin, port):
    """Return matching/empty; reject all other Serve/Funnel configurations."""
    from urllib.parse import urlsplit
    if raw in ({}, None):
        return "empty"
    authority = urlsplit(origin).netloc + ":443"
    expected = {
        "TCP": {"443": {"HTTPS": True}},
        "Web": {authority: {"Handlers": {"/": {"Proxy": f"http://127.0.0.1:{port}"}}}},
    }
    # False Funnel entries are inert, but unknown routes/settings are not ours.
    clean = dict(raw)
    funnel = clean.pop("AllowFunnel", {})
    if any(funnel.values()) or clean != expected:
        raise ValueError("Existing Tailscale Serve/Funnel settings conflict; leaving them unchanged")
    return "matching"


def write_new_or_identical(path, contents):
    path = Path(path)
    if path.exists():
        if path.read_text(encoding="utf-8-sig") != contents:
            raise ValueError("Existing setup file differs; review it before replacing")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation avoids overwriting a file created since inspection.
    with path.open("x", encoding="utf-8") as stream:
        stream.write(contents)


def task_matches(actual, expected):
    """Compare our action, principal and boot settings without default XML noise."""
    try:
        actual, expected = ET.fromstring(actual), ET.fromstring(expected)
        for path in (
            "RegistrationInfo/Description", "Principals/Principal/UserId",
            "Principals/Principal/LogonType", "Principals/Principal/RunLevel",
            "Actions/Exec/Command", "Actions/Exec/Arguments", "Actions/Exec/WorkingDirectory",
            "Triggers/BootTrigger/Delay", "Triggers/BootTrigger/Enabled",
            "Settings/MultipleInstancesPolicy", "Settings/ExecutionTimeLimit",
            "Settings/Enabled", "Settings/RestartOnFailure/Interval",
            "Settings/RestartOnFailure/Count", "Settings/RunOnlyIfNetworkAvailable",
            "Settings/DisallowStartIfOnBatteries", "Settings/StopIfGoingOnBatteries",
            "Settings/StartWhenAvailable",
        ):
            path = "/".join("t:" + part for part in path.split("/"))
            if actual.findtext(path, namespaces={"t": NS}) != expected.findtext(path, namespaces={"t": NS}):
                return False
        return all(len(actual.find("t:" + group, {"t": NS})) == 1
                   for group in ("Principals", "Triggers", "Actions"))
    except (ET.ParseError, TypeError):
        return False
