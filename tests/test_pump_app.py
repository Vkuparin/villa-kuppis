"""Tests for the Finnish pump web app (cards F2.3, F3.2, F3.3, front end).

Runs the real PumpService + PumpAppServer over HTTP on 127.0.0.1 with an
ephemeral port, fully offline against the FakeClient. Covers the page,
the view/events API, confirmed/failed/stale states through the command
API, trusted connection identity (the local actor and the Tailscale
path), error paths, the demo mode and the CLI parser.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import re
import runpy
import socket
import sqlite3
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.client import HTTPConnection
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest import mock

from villa_kuppis.pump_app import (PumpAppServer, _validate_public_origin,
                                   build_parser, main, make_demo_service,
                                   DEFAULT_LOCAL_ACTOR)
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


def http_raw(server, method, path, headers, body=b""):
    """One HTTP request with an explicit (possibly repeating) header list.

    headers is a list of (name, value) pairs; the same name may appear
    more than once, which urllib cannot express. The Host header is
    always supplied by the caller.
    """
    conn = HTTPConnection("127.0.0.1",
                          server.server_address[1], timeout=5)
    try:
        conn.putrequest(method, path, skip_host=True,
                        skip_accept_encoding=True)
        for name, value in headers:
            conn.putheader(name, value)
        conn.putheader("Connection", "close")
        conn.putheader("Content-Length", str(len(body)))
        conn.endheaders(body or None)
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


def http_raw_socket(server, request_bytes):
    """Send raw request bytes and return (status, response_body).

    For bytes that no stdlib client will put on the wire (control
    characters inside header values).
    """
    port = server.server_address[1]
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(request_bytes)
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
        head, _, rest = data.partition(b"\r\n\r\n")
        status = int(head.split(b" ", 2)[1])
        m = re.search(rb"content-length:\s*(\d+)", head, re.I)
        if m:
            need = int(m.group(1))
            while len(rest) < need:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                rest += chunk
        return status, rest


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

    def _post(self, server, unit, action, value):
        return http_json("POST", self._url(server, "/api/command"),
                         {"unit": unit, "action": action, "value": value})


class PageTests(AppTestCase):
    def test_history_selection_ignores_late_previous_response(self):
        from villa_kuppis.pump_app import render_page
        page = render_page(True)
        source = page[page.index("let histRequest ="):page.index("function populateUnits(")]
        script = """
import assert from 'node:assert/strict';
const $histUnit = {value: 'first'}, $histRange = {value: 'day'};
const $histSite = {value: 'home'}, $histStatus = {};
const $histNext = {}, $histNow = {};
let histEnd = null, histNowMs = 0, histMaxEnd = 0;
const $histChart = {}, $histLegend = {}, requests = [], drawn = [];
const drawHistory = data => drawn.push(data.unit);
const fetch = url => new Promise(resolve => requests.push({url, resolve}));
""" + source + """
const first = loadHistory();
$histUnit.value = 'second';
const second = loadHistory();
requests[1].resolve({ok: true, json: async () => ({unit: 'second'})});
await second;
requests[0].resolve({ok: true, json: async () => ({unit: 'first'})});
await first;
assert.deepEqual(drawn, ['second']);
assert.equal($histStatus.textContent, '');
"""
        result = subprocess.run(["node", "--input-type=module", "-"],
                                input=script, text=True, capture_output=True,
                                encoding="utf-8", timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

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
                       "Tiedot vanhentuneet", "Lähetetään",
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
        self.assertIn("'<td data-label=\"Milloin\" title=\"' + esc(fmtClock(ev.ts)) + '\">'", page)

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


class HistoryApiTests(AppTestCase):
    """The /api/history endpoint (card F3.3) over HTTP."""

    NOW = datetime(2026, 10, 19, 12, 0, 0, tzinfo=timezone.utc)
    PRICE_SCHEMA = """
    CREATE TABLE IF NOT EXISTS price_interval (
        ts_utc INTEGER PRIMARY KEY,
        day TEXT NOT NULL,
        price_raw_eur_mwh TEXT NOT NULL,
        retrieved_at_utc TEXT NOT NULL
    );
    """

    def _start_with_history(self, seed_prices=False):
        server, _ = self._start_app(FakeClient(make_unit_payloads()))
        service = self._services[-1]
        start = self.NOW - timedelta(seconds=24 * 3600)
        states = []
        for i in range(288):
            ts = start + timedelta(seconds=i * 300)
            states.append({
                "id": "unit-1", "power": True, "mode": "Cool",
                "setpoint": 23.0, "fan": "Auto",
                "room_temp": 20.0 + (i % 10) * 0.5,
                "updated_at": ts.isoformat(),
            })
        service.store.record_history(states)
        service.store.outdoor_upsert(
            "home",
            [(start + timedelta(hours=h), 5.0 + (h % 12) * 0.5)
             for h in range(24)])
        if seed_prices:
            db = service._prices_db
            conn = sqlite3.connect(db)
            try:
                conn.executescript(self.PRICE_SCHEMA)
                conn.executemany(
                    "INSERT INTO price_interval (ts_utc, day, "
                    "price_raw_eur_mwh, retrieved_at_utc) "
                    "VALUES (?, ?, ?, ?)",
                    [(int((start + timedelta(seconds=i * 900)).timestamp()),
                      "2026-10-18", str(100 + (i % 5) * 10),
                      self.NOW.isoformat())
                     for i in range(96)])
                conn.commit()
            finally:
                conn.close()
        return server

    def test_day_shape_and_series(self):
        server = self._start_with_history()
        status, body = http_json("GET",
                                 self._url(server, "/api/history?"
                                 "unit=unit-1&range=day&site=home"))
        self.assertEqual(status, 200)
        self.assertEqual(body["unit"], "unit-1")
        self.assertEqual(body["range"], "day")
        self.assertEqual(body["start"],
                         (self.NOW - timedelta(hours=24)).isoformat())
        self.assertEqual(body["end"], self.NOW.isoformat())
        self.assertEqual(len(body["room_temp"]), 288)
        self.assertEqual(len(body["setpoint"]), 288)
        self.assertEqual(len(body["outdoor"]), 24)
        self.assertIsNone(body["price"])
        self.assertEqual(body["room_temp"][0][1], 20.0)
        self.assertEqual(body["setpoint"][0][1], 23.0)
        # every point sits inside the requested window
        for series in (body["room_temp"], body["setpoint"], body["outdoor"]):
            for iso, _ in series:
                self.assertGreaterEqual(
                    datetime.fromisoformat(iso),
                    self.NOW - timedelta(hours=24))
                self.assertLess(datetime.fromisoformat(iso), self.NOW)

    def test_prices_included_when_stored(self):
        server = self._start_with_history(seed_prices=True)
        status, body = http_json("GET",
                                 self._url(server, "/api/history?"
                                 "unit=unit-1&range=day"))
        self.assertEqual(status, 200)
        self.assertIsInstance(body["price"], list)
        self.assertEqual(len(body["price"]), 96)
        # raw is EUR/MWh; the API reports c/kWh (raw / 10)
        self.assertEqual(body["price"][0][1], 10.0)
        self.assertEqual(body["price"][1][1], 11.0)

    def test_bad_range_400(self):
        server = self._start_with_history()
        status, body = http_json("GET",
                                 self._url(server, "/api/history?"
                                 "unit=unit-1&range=decade"))
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "bad_query")

    def test_missing_unit_400(self):
        server = self._start_with_history()
        status, body = http_json("GET",
                                 self._url(server, "/api/history"
                                 "?range=day"))
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "bad_query")

    def test_post_not_allowed_405(self):
        server = self._start_with_history()
        status, body = http_json("POST",
                                 self._url(server, "/api/history"),
                                 {"unit": "unit-1"})
        self.assertEqual(status, 405)
        self.assertEqual(body["error"], "method_not_allowed")


class CommandApiTests(AppTestCase):
    def test_cross_origin_and_rebound_host_cannot_send_commands(self):
        server, _ = self._start_app(FakeClient(make_unit_payloads()))
        service = self._services[-1]
        for headers in (
            {"Content-Type": "application/json", "Origin": "https://unrelated.example"},
            {"Content-Type": "application/json", "Host": "unrelated.example"},
            {"Content-Type": "text/plain"},
        ):
            request = urllib.request.Request(
                self._url(server, "/api/command"), method="POST",
                data=json.dumps({"unit": "test-1", "action": "power", "value": True}).encode(),
                headers=headers)
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(request, timeout=5)
            self.assertIn(ctx.exception.code, (403, 415))
        self.assertEqual(service.events(), [])

    def test_confirmed_setpoint_over_http(self):
        server, _ = self._start_app(FakeClient(make_unit_payloads()))
        self.wait_for(lambda: self._view(server)["service"]["state"] == "ok")
        status, body = self._post(server, "unit-1", "setpoint", 24.0)
        self.assertEqual(status, 202)
        self.assertEqual(body["status"], "pending")
        self.wait_for(lambda: self._unit(self._view(server), "unit-1")["setpoint"] == 24.0)
        unit = self._unit(self._view(server), "unit-1")
        self.assertIsNone(unit["pending"])
        events = self._view_events(server)
        self.assertEqual(events[0]["result"], "confirmed")
        self.assertEqual(events[0]["actor"], DEFAULT_LOCAL_ACTOR)
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


class IdentityTests(AppTestCase):
    """Trusted connection identity: local actor and the Tailscale path."""

    ORIGIN = "https://pump.tail1234.ts.net"
    AUTHORITY = "pump.tail1234.ts.net"
    LOGIN = "ville.kuparinen@example.com"

    def _start_public(self, client=None):
        client = client or FakeClient(make_unit_payloads())
        clock = FakeClock()
        service = PumpService(Path(self._tmp.name), client=client,
                              now=clock, **SERVICE_KWARGS)
        service.start()
        self._services.append(service)
        server = PumpAppServer(service, host="127.0.0.1", port=0,
                               public_origin=self.ORIGIN)
        self._servers.append(server)
        threading.Thread(target=server.serve_forever,
                         kwargs={"poll_interval": 0.1},
                         daemon=True).start()
        return server

    def _public_headers(self, *, host=None, login=None, name=None,
                        origin=None):
        headers = {}
        if host is not None:
            headers["Host"] = host
        if login is not None:
            headers["Tailscale-User-Login"] = login
        if name is not None:
            headers["Tailscale-User-Name"] = name
        if origin is not None:
            headers["Origin"] = origin
        return headers

    def _get_public(self, server, path, **kwargs):
        host = kwargs.pop("host", self.AUTHORITY)
        headers = self._public_headers(host=host, **kwargs)
        req = urllib.request.Request(self._url(server, path),
                                     headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                return res.status, res.read()
        except urllib.error.HTTPError as err:
            return err.code, err.read()

    def _post_public(self, server, body, **kwargs):
        kwargs.setdefault("host", self.AUTHORITY)
        headers = self._public_headers(**kwargs)
        headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            self._url(server, "/api/command"), method="POST",
            data=json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                return res.status, res.read()
        except urllib.error.HTTPError as err:
            return err.code, err.read()

    def _public_view(self, server):
        status, raw = self._get_public(server, "/api/view", login=self.LOGIN)
        self.assertEqual(status, 200)
        return json.loads(raw)

    def test_unknown_display_name_charset_keeps_login(self):
        server = self._start_public()
        status, raw = self._get_public(
            server, "/api/identity", login=self.LOGIN,
            name="=?unknown-charset?b?VmlsbGU=?=")
        self.assertEqual(status, 200)
        self.assertIn(self.LOGIN, json.loads(raw)["actor"])

    # --- local mode ---------------------------------------------------------

    def test_local_mode_reports_local_actor(self):
        server, _ = self._start_app(FakeClient(make_unit_payloads()))
        status, body = http_json("GET", self._url(server, "/api/identity"))
        self.assertEqual(status, 200)
        self.assertEqual(body, {"actor": DEFAULT_LOCAL_ACTOR,
                                "display_name": None, "source": "local"})

    def test_local_mode_ignores_tailscale_identity_headers(self):
        server, _ = self._start_app(FakeClient(make_unit_payloads()))
        req = urllib.request.Request(
            self._url(server, "/api/identity"),
            headers={"Tailscale-User-Login": "evil@ts.net",
                     "Tailscale-User-Name": "evil"})
        with urllib.request.urlopen(req, timeout=5) as res:
            body = json.loads(res.read())
        self.assertEqual(body["actor"], DEFAULT_LOCAL_ACTOR)
        self.assertEqual(body["source"], "local")

    def test_local_mode_duplicate_host_rejected(self):
        server, _ = self._start_app(FakeClient(make_unit_payloads()))
        host = f"127.0.0.1:{server.server_address[1]}"
        status, _ = http_raw(server, "GET", "/",
                             [("Host", host), ("Host", host)])
        self.assertEqual(status, 403)

    # --- public mode: required identity -------------------------------------

    def test_public_mode_requires_login_on_every_route(self):
        server = self._start_public()
        for path in ("/", "/api/view", "/api/events",
                     "/api/history?unit=unit-1&range=day", "/api/identity"):
            status, _ = self._get_public(server, path)
            self.assertEqual(status, 403, path)
        # loopback keeps working as the local actor even in public mode:
        # the deployed runtime administers the app from the local PC
        # while phones come through Serve.
        host = f"127.0.0.1:{server.server_address[1]}"
        status, raw = self._get_public(server, "/api/identity",
                                       host=host, login=self.LOGIN)
        body = json.loads(raw)
        self.assertEqual(status, 200)
        self.assertEqual(body["actor"], DEFAULT_LOCAL_ACTOR)
        self.assertEqual(body["source"], "local")
        # a non-loopback host that is not the configured authority is
        # refused, even with a login
        status, _ = self._get_public(server, "/api/identity",
                                     host="other.tail1234.ts.net",
                                     login=self.LOGIN)
        self.assertEqual(status, 403)
        # the right Host plus the login reaches every route
        for path in ("/", "/api/view", "/api/events", "/api/identity"):
            status, _ = self._get_public(server, path, login=self.LOGIN)
            self.assertEqual(status, 200, path)

    def test_public_mode_localhost_stays_local(self):
        server = self._start_public()
        self.wait_for(
            lambda: self._public_view(server)["service"]["state"] == "ok")
        port = server.server_address[1]
        for host in (f"127.0.0.1:{port}", f"localhost:{port}"):
            # view and identity work as the local actor
            status, _ = self._get_public(server, "/api/view", host=host)
            self.assertEqual(status, 200, host)
            status, raw = self._get_public(server, "/api/identity",
                                           host=host)
            body = json.loads(raw)
            self.assertEqual(body["actor"], DEFAULT_LOCAL_ACTOR, host)
            self.assertEqual(body["source"], "local", host)
        # forged Tailscale headers and a body user cannot pick the actor
        status, raw = self._get_public(
            server, "/api/identity", host=f"localhost:{port}",
            login="evil@ts.net", name="Evil")
        body = json.loads(raw)
        self.assertEqual(body["actor"], DEFAULT_LOCAL_ACTOR)
        self.assertIsNone(body["display_name"])
        status, _ = self._post_public(
            server, {"unit": "unit-1", "action": "setpoint",
                     "value": 24.0, "user": "spoof"},
            host=f"localhost:{port}", login="evil@ts.net")
        self.assertEqual(status, 202)
        events = self._public_events(server)
        self.assertEqual(events[0]["actor"], DEFAULT_LOCAL_ACTOR)
        # a duplicate Origin is rejected even locally
        host = f"127.0.0.1:{port}"
        status, _ = http_raw(server, "GET", "/api/identity",
                             [("Host", host),
                              ("Origin", f"http://{host}"),
                              ("Origin", f"http://{host}")])
        self.assertEqual(status, 403)
        # and a local origin that does not match the Host still is
        status, _ = http_raw(server, "GET", "/api/identity",
                             [("Host", host),
                              ("Origin", f"http://localhost:{port}")])
        self.assertEqual(status, 403)

    def test_public_mode_host_must_match_exactly(self):
        server = self._start_public()
        for host in ("pump.tail1234.ts.net:443",     # explicit port
                     "other.tail1234.ts.net",        # another tailnet node
                     "pump.ts.net.evil.example",     # suffix trick
                     "sub.pump.tail1234.ts.net"):    # subdomain
            status, _ = self._get_public(server, "/api/identity",
                                         host=host, login=self.LOGIN)
            self.assertEqual(status, 403, host)
        # DNS hostnames are case-insensitive
        status, _ = self._get_public(server, "/api/identity",
                                     host="Pump.Tail1234.TS.Net",
                                     login=self.LOGIN)
        self.assertEqual(status, 200)

    def test_public_mode_origin_must_match_exactly(self):
        server = self._start_public()
        self.wait_for(
            lambda: self._public_view(server)["service"]["state"] == "ok")
        body = {"unit": "unit-1", "action": "power", "value": True}
        # a foreign origin (or a mangled one) is refused before the service
        for origin in ("https://other.tail1234.ts.net",
                       "https://pump.tail1234.ts.net/",
                       "http://127.0.0.1:9999"):
            status, _ = self._post_public(server, body, login=self.LOGIN,
                                          origin=origin)
            self.assertEqual(status, 403, origin)
        # a matching origin is fine even in the wrong case
        status, _ = self._post_public(
            server, body, login=self.LOGIN,
            origin="HTTPS://PUMP.TAIL1234.TS.NET")
        self.assertEqual(status, 202)
        self.wait_for(lambda: (
            self._public_view(server)["units"][0]["pending"] is None
            and self._public_events(server)))
        # and an absent Origin header is allowed (curl, service-to-service)
        status, _ = self._post_public(
            server, {"unit": "unit-2", "action": "power", "value": True},
            login=self.LOGIN)
        self.assertEqual(status, 202)

    def _public_events(self, server):
        status, raw = self._get_public(server, "/api/events",
                                       login=self.LOGIN)
        self.assertEqual(status, 200)
        return json.loads(raw)

    # --- public mode: the actor is the login, never the body ----------------

    def test_public_mode_command_actor_is_the_login(self):
        server = self._start_public()
        self.wait_for(
            lambda: self._public_view(server)["service"]["state"] == "ok")
        status, _ = self._post_public(
            server, {"unit": "unit-1", "action": "setpoint", "value": 24.0,
                     "user": "spoof"},
            login=self.LOGIN)
        self.assertEqual(status, 202)
        events = self._public_events(server)
        self.assertEqual(events[0]["actor"], self.LOGIN)
        self.assertEqual(events[0]["unit"], "unit-1")

    def test_public_mode_display_name_rfc2047(self):
        server = self._start_public()
        # Q-encoded, plain ASCII display name
        status, raw = self._get_public(
            server, "/api/identity", login=self.LOGIN,
            name="=?utf-8?q?Ville_Kuparinen?=")
        body = json.loads(raw)
        self.assertEqual(status, 200)
        self.assertEqual(body["display_name"], "Ville Kuparinen")
        self.assertEqual(body["actor"], f"Ville Kuparinen ({self.LOGIN})")
        # B-encoded, non-ASCII display name
        encoded = "=?utf-8?B?" + base64.b64encode(
            "Äijä Tavoittelija".encode("utf-8")).decode("ascii") + "?="
        status, raw = self._get_public(server, "/api/identity",
                                       login=self.LOGIN, name=encoded)
        body = json.loads(raw)
        self.assertEqual(body["display_name"], "Äijä Tavoittelija")
        self.assertEqual(body["actor"],
                         f"Äijä Tavoittelija ({self.LOGIN})")

    def test_public_mode_display_name_in_events(self):
        server = self._start_public()
        self.wait_for(
            lambda: self._public_view(server)["service"]["state"] == "ok")
        status, _ = self._post_public(
            server, {"unit": "unit-1", "action": "power", "value": True},
            login=self.LOGIN, name="=?utf-8?q?Ville_Kuparinen?=")
        self.assertEqual(status, 202)
        events = self._public_events(server)
        self.assertEqual(events[0]["actor"], f"Ville Kuparinen ({self.LOGIN})")

    def test_public_mode_overlong_combined_falls_back_to_login(self):
        server = self._start_public()
        login = "l" * 40 + "@example.com"   # 55 characters
        name = "n" * 60
        # 60 + 3 + 55 = 118 > 80, so only the login is recorded
        status, raw = self._get_public(server, "/api/identity",
                                       login=login, name=name)
        body = json.loads(raw)
        self.assertEqual(status, 200)
        self.assertEqual(body["actor"], login)
        self.assertEqual(body["display_name"], name)

    # --- public mode: header validation --------------------------------------

    def test_public_mode_login_header_validation(self):
        server = self._start_public()
        for login in ("", "   ", "a" * 81):
            status, _ = self._get_public(server, "/api/identity",
                                         login=login)
            self.assertEqual(status, 403, repr(login))
        # a control character in the login is refused (raw bytes on the wire)
        req = (b"GET /api/identity HTTP/1.1\r\n"
               + f"Host: {self.AUTHORITY}\r\n".encode("ascii")
               + b"Tailscale-User-Login: bad\x01login\r\n"
               + b"Connection: close\r\n\r\n")
        status, _ = http_raw_socket(server, req)
        self.assertEqual(status, 403)
        # exactly at the length limit is fine
        status, _ = self._get_public(server, "/api/identity",
                                     login="a" * 80)
        self.assertEqual(status, 200)

    def test_public_mode_duplicate_identity_headers_rejected(self):
        server = self._start_public()
        for headers in (
            [("Host", self.AUTHORITY), ("Host", self.AUTHORITY)],
            [("Host", self.AUTHORITY),
             ("Tailscale-User-Login", self.LOGIN),
             ("Tailscale-User-Login", self.LOGIN)],
            [("Host", self.AUTHORITY),
             ("Tailscale-User-Login", self.LOGIN),
             ("Origin", self.ORIGIN), ("Origin", self.ORIGIN)],
            [("Host", self.AUTHORITY),
             ("Tailscale-User-Login", self.LOGIN),
             ("Tailscale-User-Name", "A B"),
             ("Tailscale-User-Name", "C D")],
        ):
            status, _ = http_raw(server, "GET", "/api/identity", headers)
            self.assertEqual(status, 403, headers)

    def test_public_mode_forwarded_headers_ignored(self):
        server = self._start_public()
        headers = self._public_headers(host=self.AUTHORITY,
                                       login=self.LOGIN)
        headers["X-Forwarded-For"] = "8.8.8.8"
        headers["X-Forwarded-Host"] = "evil.ts.net"
        headers["X-Forwarded-User"] = "evil@example.com"
        req = urllib.request.Request(self._url(server, "/api/identity"),
                                     headers=headers)
        with urllib.request.urlopen(req, timeout=5) as res:
            body = json.loads(res.read())
        self.assertEqual(body["actor"], self.LOGIN)
        self.assertEqual(body["source"], "tailscale")

    # --- configuration -------------------------------------------------------

    def test_public_origin_validation(self):
        for bad in ("http://pump.ts.net",
                    "https://user:pw@pump.ts.net",
                    "https://pump.ts.net/path",
                    "https://pump.ts.net?x=1",
                    "https://pump.ts.net#frag",
                    "https://pump.example.com",
                    "https://pump.ts.net.evil.example",
                    "https://pump..ts.net",
                    "https://-pump.ts.net",
                    "https://pump.-ts.net",
                    "https://pump.ts.net:99999",
                    "not a url",
                    "https://"):
            with self.assertRaises(ValueError, msg=bad):
                _validate_public_origin(bad)
        self.assertEqual(_validate_public_origin("https://pump.ts.net"),
                         ("https://pump.ts.net", "pump.ts.net"))
        self.assertEqual(_validate_public_origin("https://pump.ts.net:8443"),
                         ("https://pump.ts.net:8443", "pump.ts.net:8443"))
        self.assertEqual(_validate_public_origin("HTTPS://PUMP.TS.NET/"),
                         ("https://pump.ts.net", "pump.ts.net"))

    def test_public_origin_with_port_is_enforced(self):
        client = FakeClient(make_unit_payloads())
        clock = FakeClock()
        service = PumpService(Path(self._tmp.name), client=client,
                              now=clock, **SERVICE_KWARGS)
        service.start()
        self._services.append(service)
        server = PumpAppServer(service, host="127.0.0.1", port=0,
                               public_origin="https://pump.ts.net:8443")
        self._servers.append(server)
        threading.Thread(target=server.serve_forever,
                         kwargs={"poll_interval": 0.1},
                         daemon=True).start()
        # the authority now includes the port
        status, _ = self._get_public(server, "/api/identity",
                                     host="pump.ts.net:8443",
                                     login=self.LOGIN)
        self.assertEqual(status, 200)
        status, _ = self._get_public(server, "/api/identity",
                                     host="pump.ts.net",
                                     login=self.LOGIN)
        self.assertEqual(status, 403)

    # --- the page -------------------------------------------------------------

    def test_page_shows_identity_indicator(self):
        server, _ = self._start_app(FakeClient(make_unit_payloads()))
        _, _, raw = http("GET", self._url(server, "/"))
        page = raw.decode("utf-8")
        self.assertIn('id="identity"', page)
        self.assertIn('"/api/identity"', page)
        # the deprecated client-chosen actor field is gone from the page
        self.assertNotIn('user: ""', page)


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
        status, body = self._post(server, "demo-1", "setpoint", 24.0)
        self.assertEqual(status, 202)
        self.wait_for(lambda: self._unit(self._view(server), "demo-1")["setpoint"] == 24.0)


class CliTests(unittest.TestCase):
    def test_bind_failure_closes_service_before_start(self):
        service = mock.Mock()
        with mock.patch("villa_kuppis.pump_app.make_demo_service", return_value=service), \
             mock.patch("villa_kuppis.pump_app.PumpAppServer", side_effect=OSError("busy")):
            with self.assertRaises(OSError):
                main(["serve", "--demo", "--data-dir", "unused"])
        service.start.assert_not_called()
        service.close.assert_called_once()

    def test_bad_public_origin_rejected_before_start(self):
        service = mock.Mock()
        with mock.patch("villa_kuppis.pump_app.make_demo_service",
                        return_value=service), \
             mock.patch("sys.stderr", new=io.StringIO()) as stderr:
            code = main(["serve", "--demo", "--data-dir", "unused",
                         "--public-origin", "https://pump.example.com"])
        self.assertEqual(code, 1)
        service.start.assert_not_called()
        service.close.assert_called_once()
        self.assertIn("ts.net", stderr.getvalue())

    def test_nonlocal_bind_rejected_before_client_creation(self):
        with mock.patch("villa_kuppis.pump_app.make_live_service") as factory, \
             mock.patch("sys.stderr", new=io.StringIO()):
            self.assertEqual(main(["serve", "--live", "--host", "0.0.0.0",
                                   "--data-dir", "unused"]), 1)
        factory.assert_not_called()

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


class LauncherTests(unittest.TestCase):
    def launcher(self):
        path = Path(__file__).resolve().parents[1] / "scripts" / "start-pump-app.py"
        return runpy.run_path(str(path))["main"]

    def test_cancelled_login_opens_no_client(self):
        main_fn = self.launcher()
        factory = mock.Mock()
        with mock.patch.dict(main_fn.__globals__, {
                "gui_getpass": lambda: lambda _: "",
                "make_live_service": factory}), \
             mock.patch("sys.argv", ["launcher", "--live"]), \
             mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch("builtins.print"):
            self.assertEqual(main_fn(), 0)
        factory.assert_not_called()

    def test_live_launcher_restores_environment_and_closes(self):
        main_fn = self.launcher()
        credentials = iter(("synthetic@example.invalid", "synthetic-password"))
        service, server = mock.Mock(), mock.Mock()
        server.serve_forever.side_effect = KeyboardInterrupt
        def make_service(_):
            self.assertEqual(os.environ["VILLA_KUPPIS_MELCLOUD_USERNAME"],
                             "synthetic@example.invalid")
            self.assertNotIn("VILLA_KUPPIS_WEATHER_HOME", os.environ)
            return service
        def open_browser(_):
            self.assertNotIn("VILLA_KUPPIS_MELCLOUD_USERNAME", os.environ)
            self.assertNotIn("VILLA_KUPPIS_MELCLOUD_PASSWORD", os.environ)
        with tempfile.TemporaryDirectory() as data, \
             mock.patch.dict(os.environ, {"VILLA_KUPPIS_WEATHER_HOME": "60,25"}, clear=True), \
             mock.patch.dict(main_fn.__globals__, {
                 "gui_getpass": lambda: lambda _: next(credentials),
                 "make_live_service": make_service,
                 "PumpAppServer": mock.Mock(return_value=server)}), \
             mock.patch("sys.argv", ["launcher", "--live"]), \
             mock.patch("tempfile.mkdtemp", return_value=data), \
             mock.patch("webbrowser.open", side_effect=open_browser), mock.patch("builtins.print"):
            self.assertEqual(main_fn(), 0)
            self.assertNotIn("VILLA_KUPPIS_MELCLOUD_PASSWORD", os.environ)
            self.assertEqual(os.environ["VILLA_KUPPIS_WEATHER_HOME"], "60,25")
        service.close.assert_called_once()
        server.server_close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
