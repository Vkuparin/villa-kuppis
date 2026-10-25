"""Home Assistant app mode: ingress and Tailscale proxy identity, options, import.

The server listens on all interfaces as inside the app container. Clients
connect from other loopback addresses (127.0.0.2 stands in for the
Supervisor's ingress proxy, 127.0.0.3 for the home-server Tailscale Serve
proxy, 127.0.0.4 for any other machine), so nothing leaves the host.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path

from test_pump_app import SERVICE_KWARGS, FakeClock, make_unit_payloads
from villa_kuppis import ha_app
from villa_kuppis.pump_app import PumpAppServer, render_page
from villa_kuppis.pump_probe import FakeClient
from villa_kuppis.pump_service import PumpService
from villa_kuppis.runtime import Config

INGRESS, PROXY, STRANGER = "127.0.0.2", "127.0.0.3", "127.0.0.4"
ORIGIN, AUTHORITY = "https://home-server.example.ts.net", "home-server.example.ts.net"


class AppModeServerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.service = PumpService(Path(self._tmp.name),
                                   client=FakeClient(make_unit_payloads()),
                                   now=FakeClock(), **SERVICE_KWARGS)
        self.service.start()
        self.server = PumpAppServer(self.service, host="0.0.0.0", port=0,
                                    public_origin=ORIGIN, ingress=True,
                                    trusted_proxies=(PROXY,))
        self.server.ingress_proxy = INGRESS
        threading.Thread(target=self.server.serve_forever,
                         kwargs={"poll_interval": 0.05}, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.service.close()
        self._tmp.cleanup()

    def request(self, source, path="/api/identity", headers=None, method="GET", body=None):
        conn = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5,
                              source_address=(source, 0))
        conn.putrequest(method, path, skip_host=True)
        for name, value in (headers or {}).items():
            conn.putheader(name, value)
        if body is not None:
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", str(len(body)))
        conn.endheaders(body)
        response = conn.getresponse()
        data = response.read()
        conn.close()
        return response.status, json.loads(data) if data.startswith(b"{") else data

    def test_ingress_user_is_the_actor(self):
        status, body = self.request(INGRESS, headers={
            "Host": "home-haos.example.ts.net", "X-Remote-User-Id": "abc123",
            "X-Remote-User-Name": "ville", "X-Remote-User-Display-Name": "Ville"})
        self.assertEqual(status, 200)
        self.assertEqual((body["actor"], body["source"], body["display_name"]),
                         ("Ville", "home_assistant", "Ville"))

    def test_ingress_without_user_or_from_another_origin_is_refused(self):
        host = {"Host": "home-haos.example.ts.net"}
        self.assertEqual(self.request(INGRESS, headers=host)[0], 403)
        status, _ = self.request(INGRESS, "/api/away", method="POST", body=b'{"away": true}',
                                 headers={**host, "X-Remote-User-Id": "abc",
                                          "Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        status, _ = self.request(INGRESS, headers={**host, "X-Remote-User-Id": "abc",
                                                   "Origin": "https://home-haos.example.ts.net"})
        self.assertEqual(status, 200)

    def test_tailscale_proxy_needs_public_host_and_login(self):
        ok = {"Host": AUTHORITY, "Tailscale-User-Login": "ville@example.com"}
        status, body = self.request(PROXY, headers=ok)
        self.assertEqual((status, body["source"]), (200, "tailscale"))
        self.assertEqual(self.request(PROXY, headers={"Host": AUTHORITY})[0], 403)
        # The proxy cannot pose as the local machine.
        port = self.server.server_port
        self.assertEqual(self.request(PROXY, headers={"Host": f"127.0.0.1:{port}"})[0], 403)

    def test_other_clients_are_refused_even_with_trusted_headers(self):
        for headers in ({"Host": AUTHORITY, "Tailscale-User-Login": "ville@example.com"},
                        {"Host": "x", "X-Remote-User-Id": "abc"},
                        {"Host": f"127.0.0.1:{self.server.server_port}"}):
            self.assertEqual(self.request(STRANGER, headers=headers)[0], 403)

    def test_loopback_health_check_still_works(self):
        status, body = self.request("127.0.0.1", headers={
            "Host": f"127.0.0.1:{self.server.server_port}"})
        self.assertEqual((status, body["source"]), (200, "local"))


class AppModeConstructionTests(unittest.TestCase):
    def test_all_interfaces_only_in_app_mode(self):
        with self.assertRaises(ValueError):
            PumpAppServer(object(), host="0.0.0.0", port=0)
        with self.assertRaises(ValueError):
            PumpAppServer(object(), host="0.0.0.0", port=0, trusted_proxies=(PROXY,))

    def test_page_uses_only_relative_app_urls(self):
        page = render_page(False)
        self.assertEqual(re.findall(r'''["'(]/(?:api/|manifest|icon|sw\.js)''', page), [])


class OptionsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_full_options_make_a_valid_config_with_private_secrets(self):
        config = ha_app.build_config({
            "pumps": "melcloud", "melcloud_username": "a@example.com",
            "melcloud_password": "pw", "weather_home": "60.18,25.06",
            "cottage_url": "wss://cottage.example.ts.net/api/websocket",
            "cottage_token": "tok", "public_origin": ORIGIN,
            "tailscale_proxy": "192.168.86.36", "automation_default_on": False,
        }, self.root, "supervisor-token")
        loaded = Config.load_dict(config)
        self.assertTrue(loaded.ingress)
        self.assertEqual(loaded.trusted_proxies, ("192.168.86.36",))
        self.assertEqual(dict(loaded.sites)["home"].url, ha_app.SUPERVISOR_WS)
        self.assertEqual(loaded.data_dir, self.root / "db")
        for name in ("melcloud.json", "home-token", "cottage-token"):
            mode = stat.S_IMODE((self.root / "secrets" / name).stat().st_mode)
            self.assertEqual(mode, 0o600, name)
        self.assertNotIn("pw", json.dumps(config))

    def test_missing_melcloud_login_is_a_readable_error(self):
        with self.assertRaisesRegex(ha_app.OptionsError, "melcloud_username"):
            ha_app.build_config({"pumps": "melcloud"}, self.root, None)
        config = ha_app.build_config({"pumps": "home_assistant"}, self.root, "t")
        self.assertEqual(Config.load_dict(config).pumps, "home_assistant")

    def test_proxy_without_origin_is_refused(self):
        with self.assertRaises(ha_app.OptionsError):
            ha_app.build_config({"pumps": "home_assistant", "tailscale_proxy": "10.0.0.1"},
                                self.root, "t")

    def test_main_reports_bad_options_without_starting(self):
        (self.root / "options.json").write_text('{"pumps": "melcloud"}')
        started = []
        self.assertEqual(ha_app.main(data_root=self.root, import_dir=self.root / "import",
                                     run=started.append), 1)
        self.assertEqual(started, [])

    def test_main_runs_the_runtime_with_the_written_config(self):
        (self.root / "options.json").write_text('{"pumps": "home_assistant"}')
        calls = []
        os.environ["SUPERVISOR_TOKEN"] = "t"
        try:
            result = ha_app.main(data_root=self.root, import_dir=self.root / "import",
                                 run=lambda argv: calls.append(argv) or 0)
        finally:
            del os.environ["SUPERVISOR_TOKEN"]
        self.assertEqual(result, 0)
        self.assertEqual(calls[0][-1], "--log-stdout")
        Config.load(calls[0][1])


class ImportTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.src, self.dst = root / "import", root / "db"
        self.src.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def make_db(self, path, value):
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE t (v TEXT)")
        con.execute("INSERT INTO t VALUES (?)", (value,))
        con.commit()
        con.close()

    def test_imports_once_and_marks_the_originals(self):
        self.make_db(self.src / "pumps.db", "old history")
        self.make_db(self.src / "prices.db", "prices")
        self.assertEqual(ha_app.import_databases(self.src, self.dst), ["pumps.db", "prices.db"])
        con = sqlite3.connect(self.dst / "pumps.db")
        self.assertEqual(con.execute("SELECT v FROM t").fetchone()[0], "old history")
        con.close()
        self.assertTrue((self.src / "pumps.db.imported").exists())
        self.assertEqual(ha_app.import_databases(self.src, self.dst), [])

    def test_never_overwrites_existing_data(self):
        self.dst.mkdir()
        self.make_db(self.dst / "pumps.db", "live")
        self.make_db(self.src / "pumps.db", "old")
        self.assertEqual(ha_app.import_databases(self.src, self.dst), [])
        con = sqlite3.connect(self.dst / "pumps.db")
        self.assertEqual(con.execute("SELECT v FROM t").fetchone()[0], "live")
        con.close()
        self.assertTrue((self.src / "pumps.db").exists())

    def test_corrupt_file_is_refused(self):
        (self.src / "pumps.db").write_bytes(b"SQLite format 3\x00" + b"\x00" * 200)
        with self.assertRaises((ha_app.OptionsError, sqlite3.DatabaseError)):
            ha_app.import_databases(self.src, self.dst)
        self.assertFalse((self.dst / "pumps.db").exists())


if __name__ == "__main__":
    unittest.main()
