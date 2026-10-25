"""Offline checks of the unattended runtime: config, lifecycle and credentials."""
import asyncio
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock

from villa_kuppis.credential_file import Credentials
from villa_kuppis.runtime import Config, price_loop, run
from villa_kuppis.pump_service import make_live_service


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
            make_live_service("unused", credentials=Credentials(
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
                      "credential_file":str(Path(temp)/"melcloud"),
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
            with mock.patch("villa_kuppis.runtime.read_credentials_file") as read, \
                    mock.patch("villa_kuppis.runtime.make_live_service") as live:
                # A mocked price loop: the real one would fetch from Elering.
                run(config, stop_event=stop, server_factory=lambda *a, **k: server,
                    backup_factory=lambda *a: mock.Mock(), price_factory=mock.Mock())
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
            path.write_text(json.dumps(dict(values, plan=True,
                                            credential_file=str(Path(temp)/"melcloud"))))
            config = Config.load(path)
            stop, prices = threading.Event(), mock.Mock()
            server = mock.Mock()
            server.handle_request.side_effect = stop.set
            with mock.patch("villa_kuppis.runtime.read_credentials_file"), \
                    mock.patch("villa_kuppis.runtime.make_live_service") as live:
                run(config, stop_event=stop, server_factory=lambda *a, **k: server,
                    backup_factory=lambda *a: mock.Mock(), price_factory=prices)
            self.assertEqual(live.call_args.kwargs["automation_interval"], 60.0)
            # The price loop runs in its own thread, which may start late.
            deadline = time.monotonic() + 5
            while not prices.called and time.monotonic() < deadline:
                time.sleep(0.01)
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
                    mock.Mock(status="error", target_day="2026-10-26",
                              detail="missing interval"),
                    mock.Mock(status="ok")]

        def schedule_once():
            item = outcomes.pop(0)
            if not outcomes:
                stop.set()
            if isinstance(item, Exception):
                raise item
            return item

        service.schedule_once.side_effect = schedule_once
        with self.assertLogs(level="INFO") as logs:
            price_loop("data", stop, service_factory=lambda data: service,
                       poll_seconds=0)
        self.assertEqual(service.schedule_once.call_count, 4)
        self.assertTrue(any("2026-10-26: error: missing interval" in line
                            for line in logs.output))
        service.close.assert_called_once()

    def test_runner_cleans_up_and_does_not_read_credentials_for_demo(self):
        service, server, backup = mock.Mock(), mock.Mock(), mock.Mock()
        stop = threading.Event()
        server.handle_request.side_effect = stop.set
        with tempfile.TemporaryDirectory() as temp, \
                mock.patch("villa_kuppis.runtime.read_credentials_file") as read:
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
