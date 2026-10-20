"""Offline Windows setup, credential and unattended lifecycle checks."""
import ctypes
import asyncio
import json
import os
from pathlib import Path
import runpy
import subprocess
import tempfile
import threading
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

from villa_kuppis import windows_credentials as vault
from villa_kuppis.runtime import Config, price_loop, run
from villa_kuppis.pump_service import make_live_service
from villa_kuppis.windows_setup import (
    NS, public_origin, serve_matches_or_empty, task_xml, task_matches, write_new_or_identical,
    require_free_port,
)


class CredentialsTests(unittest.TestCase):
    def test_write_uses_generic_current_user_persistence_and_utf16(self):
        api = mock.Mock()
        observed = {}
        def write(pointer, flags):
            value = ctypes.cast(pointer, ctypes.POINTER(vault._Credential)).contents
            observed.update(username=value.UserName, password=ctypes.string_at(
                value.CredentialBlob, value.CredentialBlobSize).decode("utf-16-le"),
                kind=value.Type, persistence=value.Persist, target=value.TargetName)
            return True
        api.CredWriteW.side_effect = write
        credentials = vault.Credentials("mock@example.invalid", "salasana-ä-🔒")
        with mock.patch.object(vault, "_api", return_value=api):
            vault.write_credentials(credentials)
        self.assertEqual(observed, dict(username=credentials.username, password=credentials.password,
                                       kind=1, persistence=2, target=vault.DEFAULT_TARGET))
        self.assertNotIn(credentials.password, repr(credentials))
        self.assertNotIn(credentials.username, repr(credentials))

    def test_read_decodes_and_always_frees_native_allocation(self):
        api = mock.Mock()
        blob = ctypes.create_string_buffer("synthetic-ä".encode("utf-16-le"))
        value = vault._Credential()
        value.UserName = "mock@example.invalid"
        value.CredentialBlob = ctypes.cast(blob, ctypes.c_void_p)
        value.CredentialBlobSize = len("synthetic-ä".encode("utf-16-le"))
        def read(target, kind, flags, output):
            ctypes.cast(output, ctypes.POINTER(ctypes.POINTER(vault._Credential)))[0] = ctypes.pointer(value)
            return True
        api.CredReadW.side_effect = read
        with mock.patch.object(vault, "_api", return_value=api):
            self.assertEqual(vault.read_credentials().password, "synthetic-ä")
            value.CredentialBlobSize = 1
            with self.assertRaises(vault.CredentialError):
                vault.read_credentials()
        self.assertEqual(api.CredFree.call_count, 2)

    def test_native_error_is_sanitized_and_no_invalid_allocation_is_freed(self):
        api = mock.Mock()
        api.CredReadW.return_value = False
        api.CredWriteW.return_value = False
        with mock.patch.object(vault, "_api", return_value=api), \
                mock.patch.object(ctypes, "get_last_error", return_value=1168):
            with self.assertRaisesRegex(vault.CredentialError, "1168"):
                vault.read_credentials()
            with self.assertRaisesRegex(vault.CredentialError, "1168") as error:
                vault.write_credentials(vault.Credentials("private@example.invalid", "private-password"))
        self.assertNotIn("private", str(error.exception))
        api.CredFree.assert_not_called()


class SetupTests(unittest.TestCase):
    def test_setup_refuses_to_publish_an_occupied_local_port(self):
        socket = mock.MagicMock()
        socket.__enter__.return_value.connect_ex.return_value = 0
        with mock.patch("villa_kuppis.windows_setup.socket.socket", return_value=socket):
            with self.assertRaisesRegex(ValueError, "already in use"):
                require_free_port()

    @unittest.skipUnless(os.name == "nt", "Windows Task Scheduler schema check")
    def test_native_scheduler_accepts_task_definition_without_registration(self):
        with tempfile.TemporaryDirectory(prefix="vk-task-schema-") as temp:
            root = Path(temp)
            path = root / "task.xml"
            path.write_text(task_xml(root/"python.exe",root,root/"config.json",
                                     "S-1-5-21-123-1001"), encoding="utf-8")
            script = root / "validate.ps1"
            script.write_text('param([string]$XmlPath)\n$ErrorActionPreference="Stop"; '
                              '$scheduler=New-Object -ComObject Schedule.Service; '
                              '$scheduler.Connect(); $task=$scheduler.NewTask(0); '
                              '$task.XmlText=[IO.File]::ReadAllText($XmlPath)', encoding="utf-8")
            result = subprocess.run(["powershell.exe","-NoProfile","-File",str(script),str(path)],
                                    capture_output=True,text=True,timeout=15,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_task_has_boot_password_logon_low_privilege_and_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo with spaces"
            task = ET.fromstring(task_xml(root / "python.exe", root, root / "service.json", "synthetic-user"))
        def find(path):
            return task.findtext(path, namespaces={"t": NS})
        self.assertEqual(find("t:Principals/t:Principal/t:LogonType"), "Password")
        self.assertEqual(find("t:Principals/t:Principal/t:RunLevel"), "LeastPrivilege")
        self.assertEqual(find("t:Triggers/t:BootTrigger/t:Delay"), "PT30S")
        self.assertEqual(find("t:Settings/t:ExecutionTimeLimit"), "PT0S")
        self.assertEqual(find("t:Settings/t:MultipleInstancesPolicy"), "IgnoreNew")
        self.assertIn('"', find("t:Actions/t:Exec/t:Arguments"))

    def test_private_origin_and_serve_conflict_checks(self):
        origin = public_origin({"BackendState": "Running", "Self": {
            "Online": True, "DNSName": "test.synthetic.ts.net."}})
        self.assertEqual(origin, "https://test.synthetic.ts.net")
        self.assertEqual(serve_matches_or_empty({}, origin, 8377), "empty")
        matching = {"TCP": {"443": {"HTTPS": True}}, "Web": {
            "test.synthetic.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8377"}}}}}
        self.assertEqual(serve_matches_or_empty(matching, origin, 8377), "matching")
        matching["AllowFunnel"] = {"test.synthetic.ts.net:443": True}
        with self.assertRaises(ValueError):
            serve_matches_or_empty(matching, origin, 8377)
        with self.assertRaises(ValueError):
            serve_matches_or_empty({"TCP": {"8443": {}}}, origin, 8377)

    def test_setup_files_are_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            write_new_or_identical(path, "first")
            write_new_or_identical(path, "first")
            with self.assertRaises(ValueError):
                write_new_or_identical(path, "second")
            self.assertEqual(path.read_text(), "first")

    def test_matching_rejects_disabled_or_altered_task(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            xml = task_xml(root / "python.exe", root, root / "config.json", "synthetic-user")
        self.assertTrue(task_matches(xml, xml))
        self.assertFalse(task_matches(xml.replace("<Enabled>true", "<Enabled>false"), xml))
        self.assertFalse(task_matches(xml.replace("<Count>3", "<Count>0"), xml))

    def test_plan_has_no_writes_enrollment_or_publication(self):
        script = Path(__file__).resolve().parents[1] / "scripts/setup-pump-service.py"
        main = runpy.run_path(str(script))["main"]
        with tempfile.TemporaryDirectory() as temp:
            calls = []
            def command(args, **kwargs):
                calls.append(args)
                if "status" in args and "serve" not in args:
                    text = json.dumps({"BackendState":"Running", "Self":{
                        "Online":True, "DNSName":"test.synthetic.ts.net."}})
                elif "serve" in args:
                    text = "{}"
                else:
                    return mock.Mock(returncode=1, stdout="")
                return mock.Mock(returncode=0, stdout=text)
            with mock.patch.dict("os.environ", {"LOCALAPPDATA": temp}), \
                    mock.patch.dict(main.__globals__, {"command":command}), \
                    mock.patch("builtins.print"), \
                    mock.patch.object(Path, "mkdir") as mkdir, \
                    mock.patch.dict(main.__globals__, {"write_credentials":mock.Mock()}) as _:
                self.assertEqual(main(["plan", "--backup-dir", str(Path(temp) / "backup")]), 0)
                mkdir.assert_not_called()
                main.__globals__["write_credentials"].assert_not_called()
            self.assertFalse(any("set" in c or "--bg" in c for c in calls))

    def test_apply_rechecks_serve_after_enrollment_before_mutating_it(self):
        script = Path(__file__).resolve().parents[1] / "scripts/setup-pump-service.py"
        main = runpy.run_path(str(script))["main"]
        calls, inspections = [], []
        def command(args, **kwargs):
            calls.append(args)
            if "serve" in args:
                inspections.append(True)
                text = "{}" if len(inspections) == 1 else '{"TCP":{"8443":{}}}'
            elif "status" in args:
                text = json.dumps({"BackendState":"Running", "Self":{
                    "Online":True, "DNSName":"test.synthetic.ts.net."}})
            elif args[0] == "schtasks.exe":
                return mock.Mock(returncode=1, stdout="")
            elif "/user" in args:
                text = '"synthetic-user","S-1-5-21-123"'
            else:
                text = "synthetic-user"
            return mock.Mock(returncode=0, stdout=text)
        with tempfile.TemporaryDirectory() as temp, \
                mock.patch.dict("os.environ", {"LOCALAPPDATA":temp}), \
                mock.patch.dict(main.__globals__, {
                    "command":command, "write_new_or_identical":mock.Mock(),
                    "write_credentials":mock.Mock(), "require_free_port":mock.Mock()}), \
                mock.patch("runpy.run_path", return_value={"gui_getpass":
                    lambda **kwargs: lambda _:"synthetic"}), \
                mock.patch("builtins.print"):
            with self.assertRaisesRegex(ValueError, "conflict"):
                main(["apply", "--backup-dir", str(Path(temp)/"backup")])
        self.assertEqual(len(inspections), 2)
        self.assertFalse(any("set" in c or "--bg" in c for c in calls))

    def test_apply_keeps_credentials_out_of_files_and_subprocess_arguments(self):
        script = Path(__file__).resolve().parents[1] / "scripts/setup-pump-service.py"
        main = runpy.run_path(str(script))["main"]
        published = []
        shell = mock.Mock()
        shell.ShellExecuteW.return_value = 33
        credentials = iter(("mock@example.invalid", "synthetic-password"))
        matching = {"TCP":{"443":{"HTTPS":True}},"Web":{
            "test.synthetic.ts.net:443":{"Handlers":{"/":{"Proxy":"http://127.0.0.1:8377"}}}}}
        def command(args, **kwargs):
            if "serve" in args:
                text = json.dumps(matching if published else {})
            elif "status" in args:
                text = json.dumps({"BackendState":"Running", "Self":{
                    "Online":True,"DNSName":"test.synthetic.ts.net."}})
            elif args[0] == "schtasks.exe":
                return mock.Mock(returncode=1,stdout="")
            elif "/user" in args:
                text = '"synthetic-user","S-1-5-21-123-1001"'
            else:
                text = "synthetic-user"
            return mock.Mock(returncode=0,stdout=text)
        def publish(args):
            published.append(args)
            return mock.Mock(returncode=0)
        with tempfile.TemporaryDirectory() as temp, \
                mock.patch.dict("os.environ", {"LOCALAPPDATA":temp}), \
                mock.patch.dict(main.__globals__, {
                    "command":command, "write_credentials":mock.Mock(),
                    "require_free_port":mock.Mock()}), \
                mock.patch("runpy.run_path", return_value={"gui_getpass":
                    lambda **kwargs: lambda _:next(credentials)}), \
                mock.patch("subprocess.run",side_effect=publish), \
                mock.patch("ctypes.WinDLL",return_value=shell), \
                mock.patch("builtins.print"):
            self.assertEqual(main(["apply","--backup-dir",str(Path(temp)/"backups")]),0)
            for path in (Path(temp)/"VillaKuppis").iterdir():
                self.assertNotIn("synthetic-password",path.read_text())
                self.assertNotIn("mock@example.invalid",path.read_text())
        for call in shell.ShellExecuteW.call_args_list:
            self.assertNotIn("synthetic-password",repr(call))
            self.assertNotIn("mock@example.invalid",repr(call))
        self.assertEqual(shell.ShellExecuteW.call_args.args[1:3],("runas","powershell.exe"))


class RuntimeTests(unittest.TestCase):
    def test_explicit_credentials_and_weather_ignore_development_environment(self):
        captured = {}
        def service(data, **kwargs):
            captured.update(kwargs)
            return mock.Mock()
        client = mock.Mock()
        client.__aexit__ = mock.AsyncMock()
        close = mock.AsyncMock()
        with mock.patch.dict("os.environ", {
                "VILLA_KUPPIS_MELCLOUD_USERNAME":"wrong@example.invalid",
                "VILLA_KUPPIS_MELCLOUD_PASSWORD":"wrong-password",
                "VILLA_KUPPIS_WEATHER_HOME":"60,25"}, clear=True), \
                mock.patch("villa_kuppis.pump_service.PumpService", side_effect=service), \
                mock.patch("villa_kuppis.pump_service._default_client_factory",
                           return_value=(client,close)) as factory:
            make_live_service("unused", credentials=vault.Credentials(
                "stored@example.invalid","stored-password"), weather_sites={})
            self.assertEqual(captured["weather_sites"], {})
            async def create_and_close():
                _, teardown = captured["client_factory"]()
                await teardown()
            asyncio.run(create_and_close())
            factory.assert_called_once_with("stored@example.invalid","stored-password")
            close.assert_awaited_once()

    def test_config_requires_absolute_separate_paths_and_no_secret_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "service.json"
            values = {"data_dir":str(Path(temp)/"data"),"backup_dir":str(Path(temp)/"backups")}
            path.write_text(json.dumps(values))
            self.assertEqual(Config.load(path).port, 8377)
            for bad in (dict(values, password="not-a-secret"), dict(values, data_dir="relative"),
                        dict(values, backup_dir=values["data_dir"])):
                path.write_text(json.dumps(bad))
                with self.assertRaises(ValueError):
                    Config.load(path)

    def test_config_weather_sites_reach_the_live_service(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "service.json"
            values = {"data_dir":str(Path(temp)/"data"),"backup_dir":str(Path(temp)/"backups"),
                      "weather":{"home":"60.2,24.9"}}
            path.write_text(json.dumps(values))
            config = Config.load(path)
            self.assertEqual(config.weather, (("home",(60.2,24.9)),))
            for bad in ({"sauna":"60,25"}, {"home":"north"}, ["60,25"]):
                path.write_text(json.dumps(dict(values, weather=bad)))
                with self.assertRaises(ValueError):
                    Config.load(path)
            stop = threading.Event()
            server = mock.Mock()
            server.handle_request.side_effect = stop.set
            with mock.patch("villa_kuppis.runtime.read_credentials") as read, \
                    mock.patch("villa_kuppis.runtime.make_live_service") as live:
                run(config, stop_event=stop, server_factory=lambda *a, **k: server,
                    backup_factory=lambda *a: mock.Mock())
            self.assertEqual(live.call_args.kwargs["weather_sites"], {"home":(60.2,24.9)})
            read.assert_called_once()

    def test_plan_setting_enables_automation_and_prices_load_only_live(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "service.json"
            values = {"data_dir":str(Path(temp)/"data"),"backup_dir":str(Path(temp)/"backups")}
            path.write_text(json.dumps(values))
            self.assertFalse(Config.load(path).plan)
            path.write_text(json.dumps(dict(values, plan="yes")))
            with self.assertRaises(ValueError):
                Config.load(path)
            path.write_text(json.dumps(dict(values, plan=True)))
            config = Config.load(path)
            stop, prices = threading.Event(), mock.Mock()
            server = mock.Mock()
            server.handle_request.side_effect = stop.set
            with mock.patch("villa_kuppis.runtime.read_credentials"), \
                    mock.patch("villa_kuppis.runtime.make_live_service") as live:
                run(config, stop_event=stop, server_factory=lambda *a, **k: server,
                    backup_factory=lambda *a: mock.Mock(), price_factory=prices)
            self.assertEqual(live.call_args.kwargs["automation_interval"], 60.0)
            prices.assert_called_once()
            demo_prices = mock.Mock()
            run(Config(Path(temp)/"d2", Path(temp)/"b2"), demo=True, stop_event=threading.Event(),
                service_factory=lambda _: mock.Mock(),
                server_factory=lambda *a, **k: mock.Mock(handle_request=mock.Mock(side_effect=KeyboardInterrupt)),
                backup_factory=lambda *a: mock.Mock(), price_factory=demo_prices)
            demo_prices.assert_not_called()

    def test_price_loop_keeps_checking_after_errors_and_closes_service(self):
        stop = threading.Event()
        service = mock.Mock()
        outcomes = [RuntimeError("boom"), mock.Mock(status="waiting"),
                    mock.Mock(status="ok")]

        def schedule_once():
            item = outcomes.pop(0)
            if not outcomes:
                stop.set()
            if isinstance(item, Exception):
                raise item
            return item

        service.schedule_once.side_effect = schedule_once
        price_loop("data", stop, service_factory=lambda data: service,
                   poll_seconds=0)
        self.assertEqual(service.schedule_once.call_count, 3)
        service.close.assert_called_once()

    def test_runner_cleans_up_and_does_not_read_credentials_for_demo(self):
        service, server, backup = mock.Mock(), mock.Mock(), mock.Mock()
        stop = threading.Event()
        server.handle_request.side_effect = stop.set
        with tempfile.TemporaryDirectory() as temp, \
                mock.patch("villa_kuppis.runtime.read_credentials") as read:
            run(Config(Path(temp)/"data", Path(temp)/"backups"), demo=True,
                stop_event=stop, service_factory=lambda _:service,
                server_factory=lambda *a, **kw:server, backup_factory=lambda *a:backup)
            read.assert_not_called()
        service.start.assert_called_once()
        backup.start.assert_called_once()
        backup.close.assert_called_once()
        server.server_close.assert_called_once()
        service.close.assert_called_once()

    def test_bind_failure_closes_service_without_start_or_backup(self):
        service, backups = mock.Mock(), mock.Mock()
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(OSError):
                run(Config(Path(temp)/"data", Path(temp)/"backup"), demo=True,
                    service_factory=lambda _:service, server_factory=mock.Mock(side_effect=OSError()),
                    backup_factory=backups)
        service.start.assert_not_called()
        service.close.assert_called_once()
        backups.assert_not_called()

    def test_backup_close_failure_still_closes_service(self):
        service, server, backups = mock.Mock(), mock.Mock(), mock.Mock()
        stop = threading.Event()
        server.handle_request.side_effect = stop.set
        backups.close.side_effect = RuntimeError("synthetic close failure")
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(RuntimeError):
                run(Config(Path(temp)/"data", Path(temp)/"backup"), demo=True,
                    stop_event=stop, service_factory=lambda _:service,
                    server_factory=lambda *a, **kw:server, backup_factory=lambda *a:backups)
        service.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
