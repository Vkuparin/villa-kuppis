"""Tests for the Finnish pump web app (card F2.3, front end).

Runs the real PumpService + PumpAppServer over HTTP on 127.0.0.1 with an
ephemeral port, fully offline against the FakeClient. Covers the page,
the view/events API, confirmed/failed/stale states through the command
API, error paths, the demo mode and the CLI parser.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest import mock

from villa_kuppis.pump_app import (PumpAppServer, build_parser, main,
                                   make_demo_service)
from villa_kuppis.pump_probe import FakeClient, _unit_payload
from villa_kuppis.pump_service import PumpService

SERVICE_KWARGS = dict(
    poll_interval=0.05,
    stale_after=0.5,
    readback_polls=3,
    readback_delay=0.02,
    command_timeout=2.0,
)


class FakeClock:
    def __init__(self):
        self._now = datetime(2026, 10, 19, 12, 0, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self._now

    def advance(self, seconds):
        self._now = self._now + timedelta(seconds=seconds)


class FlakyFake(FakeClient):
    """A FakeClient whose context reads fail while `broken` is set."""

    def __init__(self, payloads):
        super().__init__(payloads)
        self.broken = False

    async def get_context(self):
        if self.broken:
            raise RuntimeError("no cloud")
        return await super().get_context()


class GatedFake(FakeClient):
    """A FakeClient whose control calls wait until the gate is released."""

    def __init__(self, payloads, **kwargs):
        super().__init__(payloads, **kwargs)
        self.gate = asyncio.Event()

    async def control_ata_unit(self, *args, **kwargs):
        await self.gate.wait()
        return await super().control_ata_unit(*args, **kwargs)


def make_unit_payloads():
    return [
        _unit_payload("unit-1", "Makuuhuone", mode="Cool", power="True",
                      set_temp="23.5", room="26", fan="Auto"),
        _unit_payload("unit-2", "Keittiö", mode="Cool", power="False",
                      set_temp="22", room="27", fan="Two"),
        _unit_payload("unit-3", "Vessa", mode="Fan", power="True",
                      set_temp="23", room="25", fan="One"),
    ]


def http(method, url, body=None):
    """One HTTP request; returns (status, content_type, body_bytes)."""
    data = None
    headers = {}
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as res:
            return res.status, res.headers.get("Content-Type", ""), res.read()
    except urllib.error.HTTPError as err:
        return err.code, err.headers.get("Content-Type", ""), err.read()


def http_json(method, url, body=None):
    status, ctype, raw = http(method, url, body)
    assert "application/json" in ctype
    return status, json.loads(raw)


class AppTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="vk-pump-app-")
        self._servers = []
        self._services = []

    def tearDown(self):
        for server in reversed(self._servers):
            try:
                server.shutdown()
            except Exception:
                pass
            try:
                server.server_close()
            except Exception:
                pass
        for service in reversed(self._services):
            try:
                service.close()
            except Exception:
                pass
        self._tmp.cleanup()

    def wait_for(self, condition, timeout=5.0, message="condition not met"):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.01)
        self.fail(message)

    def _start_app(self, client, *, demo=False, clock=None, **overrides):
        clock = clock or FakeClock()
        kwargs: dict[str, Any] = dict(SERVICE_KWARGS)
        kwargs.update(overrides)
        service = PumpService(Path(self._tmp.name), client=client,
                              now=clock, **kwargs)
        service.start()
        self._services.append(service)
        server = PumpAppServer(service, host="127.0.0.1", port=0, demo=demo)
        self._servers.append(server)
        threading.Thread(target=server.serve_forever,
                         kwargs={"poll_interval": 0.1},
                         daemon=True).start()
        return server, clock

    def _url(self, server, path):
        return f"http://127.0.0.1:{server.server_address[1]}{path}"

    def _view(self, server):
        status, view = http_json("GET", self._url(server, "/api/view"))
        self.assertEqual(status, 200)
        return view

    def _unit(self, view, unit_id):
        return next(u for u in view["units"] if u["id"] == unit_id)

    def _post(self, server, unit, action, value, user=None):
        body = {"unit": unit, "action": action, "value": value}
        if user is not None:
            body["user"] = user
        return http_json("POST", self._url(server, "/api/command"), body)


class PageTests(AppTestCase):
    def test_serves_finnish_page(self):
        server, _ = self._start_app(FakeClient(make_unit_payloads()))
        status, ctype, raw = http("GET", self._url(server, "/"))
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertIn("utf-8", ctype)
        page = raw.decode("utf-8")
        for needle in ('lang="fi"', "<title>Pumput</title>",
                       "Mitattu lämpötila", "Tavoitelämpötila",
                       "Toimintatila", "Puhallus", "Tapahtumat",
                       "Vanhentunut mittaus", "Lähetetään",
                       "Europe/Helsinki", "fi-FI"):
            self.assertIn(needle, page)

    def test_page_has_no_external_resources(self):
        server, _ = self._start_app(FakeClient(make_unit_payloads()))
        _, _, raw = http("GET", self._url(server, "/"))
        page = raw.decode("utf-8")
        for needle in ("src=", 'link rel', "@import", "fetch(\"http"):
            self.assertNotIn(needle, page)

    def test_page_script_source_intact(self):
        # The page ships as a Python string; a broken escape would corrupt
        # the embedded JS and silently leave the app un-rendered.
        server, _ = self._start_app(FakeClient(make_unit_payloads()))
        _, _, raw = http("GET", self._url(server, "/"))
        page = raw.decode("utf-8")
        self.assertIn("'<td title=\"' + esc(fmtClock(ev.ts)) + '\">'", page)

    def test_demo_flag(self):
        server, _ = self._start_app(FakeClient(make_unit_payloads()), demo=True)
        _, _, raw = http("GET", self._url(server, "/"))
        self.assertIn('data-demo="1"', raw.decode("utf-8"))
        server2, _ = self._start_app(FakeClient(make_unit_payloads()), demo=False)
        _, _, raw2 = http("GET", self._url(server2, "/"))
        self.assertIn('data-demo="0"', raw2.decode("utf-8"))


class ViewApiTests(AppTestCase):
    def test_view_shape_over_http(self):
        server, _ = self._start_app(FakeClient(make_unit_payloads()))
        view = None

        def ready():
            nonlocal view
            view = self._view(server)
            return view["service"]["state"] == "ok"

        self.wait_for(ready)
        assert view is not None
        self.assertEqual(len(view["units"]), 3)
        unit = self._unit(view, "unit-1")
        self.assertEqual(unit["name"], "Makuuhuone")
        self.assertTrue(unit["fresh"])
        self.assertEqual(unit["room_temp"], 26.0)
        self.assertEqual(unit["setpoint"], 23.5)
        self.assertEqual(unit["mode"], "Cool")
        self.assertEqual(unit["power"], True)
        self.assertIn("min_cool", unit["caps"])
        self.assertIn("max_cool", unit["caps"])
        self.assertIsNone(unit["pending"])

    def test_events_endpoint_empty(self):
        server, _ = self._start_app(FakeClient(make_unit_payloads()))
        self.wait_for(lambda: self._view(server)["service"]["state"] == "ok")
        status, events = http_json("GET", self._url(server, "/api/events"))
        self.assertEqual(status, 200)
        self.assertEqual(events, [])


class CommandApiTests(AppTestCase):
    def test_confirmed_setpoint_over_http(self):
        server, _ = self._start_app(FakeClient(make_unit_payloads()))
        self.wait_for(lambda: self._view(server)["service"]["state"] == "ok")
        status, body = self._post(server, "unit-1", "setpoint", 24.0,
                                  user="test")
        self.assertEqual(status, 202)
        self.assertEqual(body["status"], "pending")
        self.wait_for(lambda: self._unit(self._view(server), "unit-1")["setpoint"] == 24.0)
        unit = self._unit(self._view(server), "unit-1")
        self.assertIsNone(unit["pending"])
        events = self._view_events(server)
        self.assertEqual(events[0]["result"], "confirmed")
        self.assertEqual(events[0]["actor"], "test")
        self.assertEqual(events[0]["requested"], 24.0)
        self.assertEqual(events[0]["unit"], "unit-1")

    def _view_events(self, server):
        status, events = http_json("GET", self._url(server, "/api/events"))
        self.assertEqual(status, 200)
        return events

    def test_failed_command_over_http(self):
        # The cloud "refuses" the write: the read-backs keep seeing the
        # old value, so the change must end up as a failed event.
        def revert(payload):
            settings = {s["name"]: s["value"] for s in payload["settings"]}
            settings["SetTemperature"] = "23.5"
            payload["settings"] = [
                {"name": n, "value": v} for n, v in settings.items()
            ]

        client = FakeClient(make_unit_payloads(), mutates=[revert])
        server, _ = self._start_app(client)
        self.wait_for(lambda: self._view(server)["service"]["state"] == "ok")
        status, body = self._post(server, "unit-1", "setpoint", 24.0)
        self.assertEqual(status, 202)
        self.wait_for(lambda: (
            self._view_events(server)
            and self._view_events(server)[0]["result"] == "failed"
        ))
        event = self._view_events(server)[0]
        self.assertIn("not confirmed", event["detail"])
        self.assertEqual(self._unit(self._view(server), "unit-1")["setpoint"],
                         23.5)

    def test_stale_over_http(self):
        client = FlakyFake(make_unit_payloads())
        server, clock = self._start_app(client)
        self.wait_for(lambda: self._view(server)["service"]["state"] == "ok")
        client.broken = True
        clock.advance(10)
        self.wait_for(lambda: self._view(server)["service"]["state"] == "stale")
        view = self._view(server)
        for unit in view["units"]:
            self.assertFalse(unit["fresh"])

    def test_unknown_unit_404(self):
        server, _ = self._start_app(FakeClient(make_unit_payloads()))
        self.wait_for(lambda: self._view(server)["service"]["state"] == "ok")
        status, body = self._post(server, "nope", "power", False)
        self.assertEqual(status, 404)
        self.assertEqual(body["error"], "unknown_unit")

    def test_bad_action_400(self):
        server, _ = self._start_app(FakeClient(make_unit_payloads()))
        self.wait_for(lambda: self._view(server)["service"]["state"] == "ok")
        status, body = self._post(server, "unit-1", "teleport", True)
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "unknown_action")

    def test_out_of_bounds_400(self):
        server, _ = self._start_app(FakeClient(make_unit_payloads()))
        self.wait_for(lambda: self._view(server)["service"]["state"] == "ok")
        status, body = self._post(server, "unit-1", "setpoint", 99.0)
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "out_of_bounds")

    def test_fan_mode_400(self):
        server, _ = self._start_app(FakeClient(make_unit_payloads()))
        self.wait_for(lambda: self._view(server)["service"]["state"] == "ok")
        status, body = self._post(server, "unit-3", "setpoint", 23.0)
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "fan_mode")

    def test_pending_conflict_409(self):
        client = GatedFake(make_unit_payloads())
        server, _ = self._start_app(client)
        self.wait_for(lambda: self._view(server)["service"]["state"] == "ok")
        status, body = self._post(server, "unit-1", "setpoint", 24.0)
        self.assertEqual(status, 202)
        self.wait_for(lambda:
                      self._unit(self._view(server), "unit-1")["pending"])
        status, body = self._post(server, "unit-1", "setpoint", 24.5)
        self.assertEqual(status, 409)
        self.assertEqual(body["error"], "pending")
        client.gate.set()
        self.wait_for(lambda: (
            self._view_events(server)
            and self._view_events(server)[0]["result"] == "confirmed"
        ))

    def test_bad_body_400(self):
        server, _ = self._start_app(FakeClient(make_unit_payloads()))
        self.wait_for(lambda: self._view(server)["service"]["state"] == "ok")
        status, ctype, raw = http("POST", self._url(server, "/api/command"),
                                  b"not json")
        self.assertEqual(status, 400)
        self.assertIn("application/json", ctype)
        self.assertEqual(json.loads(raw)["error"], "bad_body")

    def test_method_not_allowed_405(self):
        server, _ = self._start_app(FakeClient(make_unit_payloads()))
        status, ctype, raw = http("POST", self._url(server, "/"),
                                  {"unit": "unit-1", "action": "power",
                                   "value": False})
        self.assertEqual(status, 405)
        self.assertEqual(json.loads(raw)["error"], "method_not_allowed")
        status, ctype, raw = http("GET", self._url(server, "/api/command"))
        self.assertEqual(status, 405)
        status, ctype, raw = http("POST", self._url(server, "/api/view"), b"{}")
        self.assertEqual(status, 405)
        self.assertEqual(json.loads(raw)["error"], "method_not_allowed")

    def test_post_unknown_path_404(self):
        # POST follows do_GET's routing: unknown API paths get a JSON 404,
        # non-API paths a plain 404, never a blanket 405.
        server, _ = self._start_app(FakeClient(make_unit_payloads()))
        self.wait_for(lambda: self._view(server)["service"]["state"] == "ok")
        status, ctype, raw = http("POST", self._url(server, "/api/nope"),
                                  b"{}")
        self.assertEqual(status, 404)
        self.assertIn("application/json", ctype)
        self.assertEqual(json.loads(raw)["error"], "not_found")
        status, ctype, raw = http("POST", self._url(server, "/definitely/not"),
                                  b"{}")
        self.assertEqual(status, 404)
        self.assertNotIn("application/json", ctype)

    def test_unknown_path_404(self):
        server, _ = self._start_app(FakeClient(make_unit_payloads()))
        status, ctype, raw = http("GET", self._url(server, "/nope"))
        self.assertEqual(status, 404)
        status, ctype, raw = http("GET", self._url(server, "/api/nope"))
        self.assertEqual(status, 404)
        self.assertIn("application/json", ctype)


class DemoTests(AppTestCase):
    def test_demo_serves_three_units(self):
        service = make_demo_service(self._tmp.name)
        self._services.append(service)
        service.start()
        server = PumpAppServer(service, host="127.0.0.1", port=0, demo=True)
        self._servers.append(server)
        threading.Thread(target=server.serve_forever,
                         kwargs={"poll_interval": 0.1},
                         daemon=True).start()
        self.wait_for(lambda: self._view(server)["service"]["state"] == "ok")
        view = self._view(server)
        self.assertEqual([u["id"] for u in view["units"]],
                         ["demo-1", "demo-2", "demo-3"])
        self.assertEqual(view["units"][0]["name"], "Demo-yksikkö 1")

    def test_demo_command_roundtrip(self):
        service = make_demo_service(self._tmp.name)
        self._services.append(service)
        service.start()
        server = PumpAppServer(service, host="127.0.0.1", port=0, demo=True)
        self._servers.append(server)
        threading.Thread(target=server.serve_forever,
                         kwargs={"poll_interval": 0.1},
                         daemon=True).start()
        self.wait_for(lambda: self._view(server)["service"]["state"] == "ok")
        status, body = self._post(server, "demo-1", "setpoint", 24.0,
                                  user="demo")
        self.assertEqual(status, 202)
        self.wait_for(lambda: self._unit(self._view(server), "demo-1")["setpoint"] == 24.0)


class CliTests(unittest.TestCase):
    def test_parser_requires_mode(self):
        with self.assertRaises(SystemExit) as ctx:
            build_parser().parse_args(["serve", "--data-dir", "x"])
        self.assertEqual(ctx.exception.code, 2)

    def test_help_exits_zero(self):
        with self.assertRaises(SystemExit) as ctx:
            build_parser().parse_args(["--help"])
        self.assertEqual(ctx.exception.code, 0)

    def test_serve_live_missing_credentials(self):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("VILLA_KUPPIS_MELCLOUD_")}
        with mock.patch.dict(os.environ, env, clear=True):
            with tempfile.TemporaryDirectory(prefix="vk-pump-cli-") as tmp:
                with mock.patch("sys.stderr", new=io.StringIO()) as stderr:
                    code = main(["serve", "--data-dir", tmp, "--live",
                                 "--port", "0"])
        self.assertEqual(code, 1)
        self.assertIn("environment variables", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
