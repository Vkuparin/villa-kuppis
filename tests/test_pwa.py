"""Tests for the installable PWA files of the pump app (card H3.1).

Localhost only: manifest, icons, service worker and the page links, plus a
node run of the service worker's fetch handler against fake caches.
"""

from __future__ import annotations

import json
import struct
import subprocess
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path

from villa_kuppis.pump_app import PumpAppServer, render_page
from villa_kuppis.pump_probe import FakeClient
from villa_kuppis.pump_service import PumpService

from tests.test_pump_app import (SERVICE_KWARGS, AppTestCase, FakeClock, http,
                                 http_raw, make_unit_payloads)

STATIC = Path(__file__).resolve().parent.parent / "villa_kuppis" / "static"


def png_size(raw: bytes) -> tuple[int, int]:
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    assert raw[12:16] == b"IHDR"
    return struct.unpack(">II", raw[16:24])


def get_with_headers(server, path):
    conn = HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    try:
        conn.request("GET", path)
        res = conn.getresponse()
        return res.status, dict(res.getheaders()), res.read()
    finally:
        conn.close()


class PwaFilesTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.server, _ = self._start_app(FakeClient(make_unit_payloads()))

    def test_manifest_fields(self):
        status, headers, raw = get_with_headers(self.server, "/manifest.webmanifest")
        self.assertEqual(status, 200)
        self.assertIn("application/manifest+json", headers["Content-Type"])
        self.assertEqual(headers["Cache-Control"], "no-cache")
        m = json.loads(raw)
        self.assertEqual(m["name"], "Villa Kuppis")
        self.assertEqual(m["short_name"], "Kuppis")
        self.assertEqual(m["lang"], "fi")
        self.assertEqual(m["start_url"], "./")
        self.assertEqual(m["scope"], "./")
        self.assertEqual(m["display"], "standalone")
        self.assertEqual(m["theme_color"], "#0b5fff")
        self.assertTrue(m["background_color"].startswith("#"))
        purposes = {(i["sizes"], i["purpose"]) for i in m["icons"]}
        self.assertIn(("192x192", "any"), purposes)
        self.assertIn(("512x512", "any"), purposes)
        self.assertIn(("512x512", "maskable"), purposes)
        for icon in m["icons"]:
            status, _, _ = http("GET", self._url(self.server, "/" + icon["src"]))
            self.assertEqual(status, 200, icon["src"])

    def test_icons(self):
        for path, size in (("/icon-192.png", 192), ("/icon-512.png", 512),
                           ("/icon-maskable-512.png", 512)):
            status, headers, raw = get_with_headers(self.server, path)
            self.assertEqual(status, 200)
            self.assertEqual(headers["Content-Type"], "image/png")
            self.assertEqual(png_size(raw), (size, size))
        status, headers, raw = get_with_headers(self.server, "/icon.svg")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "image/svg+xml")
        self.assertIn(b"<svg", raw)

    def test_service_worker_headers(self):
        status, headers, raw = get_with_headers(self.server, "/sw.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", headers["Content-Type"])
        self.assertEqual(headers["Cache-Control"], "no-cache")
        self.assertEqual(raw, (STATIC / "sw.js").read_bytes())

    def test_service_worker_source_never_touches_api_or_posts(self):
        source = (STATIC / "sw.js").read_text(encoding="utf-8")
        # "api/" appears only in the bypass; nothing is cached from it.
        self.assertEqual(source.count('"api/"'), 1)
        self.assertIn('startsWith(BASE + "api/")', source)
        self.assertIn('request.method !== "GET"', source)
        self.assertNotIn("sync", source.lower())
        self.assertIn("caches.delete", source)

    def test_service_worker_fetch_handler_in_node(self):
        script = """
const fs = require('fs'), vm = require('vm');
const src = fs.readFileSync(process.argv[1], 'utf8');
const handlers = {}, store = {}, deleted = [];
const cacheObj = { addAll: async () => {}, put: async (k, v) => { store[k] = v; } };
const self_ = { location: { origin: 'http://x', href: 'http://x' + process.argv[2] }, addEventListener: (n, f) => { handlers[n] = f; },
                skipWaiting: async () => {}, clients: { claim: async () => {} } };
const ctx = { self: self_, URL, Response: { error: () => 'ERR' },
  caches: { open: async () => cacheObj, match: async k => store[k] ? 'CACHED' : undefined,
            keys: async () => ['vk-shell-v1', 'vk-shell-v2'], delete: async k => { deleted.push(k); } },
  fetch: async () => { if (ctx.offline) throw new Error('offline');
    return { ok: true, clone: () => 'COPY' }; }, offline: false };
vm.runInNewContext(src, ctx);
function run(method, path) {
  let responded = null;
  handlers.fetch({ request: { method, url: 'http://x' + path },
                   respondWith: p => { responded = p; } });
  return responded;
}
(async () => {
  const out = {};
  out.post = run('POST', '/') === null;
  out.api = run('GET', '/api/view') === null;
  out.other = run('GET', '/something') === null;
  const online = run('GET', '/');
  out.online = online !== null && (await online).ok === true;
  await new Promise(r => setTimeout(r, 5));
  out.cachedShell = Object.keys(store).join();
  ctx.offline = true;
  out.offlineShell = await run('GET', '/');
  out.offlineIcon = await run('GET', '/icon.svg');
  let wait; handlers.activate({ waitUntil: p => { wait = p; } }); await wait;
  out.deleted = deleted.join();
  console.log(JSON.stringify(out));
})();
"""
        for base in ("/", "/api/hassio_ingress/abc/"):
            with self.subTest(base=base):
                self._check_worker(script, base)

    def _check_worker(self, script, base):
        script = script.replace("'http://x' + path", "'http://x' + base + path.slice(1)")
        script = script.replace("const src", "const base = process.argv[3];\nconst src")
        result = subprocess.run(["node", "-e", script, str(STATIC / "sw.js"),
                                 base + "sw.js", base],
                                text=True, capture_output=True, encoding="utf-8",
                                timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertTrue(out["post"])
        self.assertTrue(out["api"])
        self.assertTrue(out["other"])
        self.assertTrue(out["online"])
        self.assertEqual(out["cachedShell"], base)
        self.assertEqual(out["offlineShell"], "CACHED")
        self.assertEqual(out["offlineIcon"], "ERR")  # never cached while online
        self.assertEqual(out["deleted"], "vk-shell-v1")

    def test_page_links_manifest_and_registers_worker(self):
        _, _, raw = http("GET", self._url(self.server, "/"))
        page = raw.decode("utf-8")
        for needle in ('<link rel="manifest" href="manifest.webmanifest">',
                       '<meta name="theme-color" content="#0b5fff">',
                       '<link rel="apple-touch-icon" href="icon-192.png">',
                       'navigator.serviceWorker.register("sw.js", { scope: "./" })',
                       'id="health-home"', 'id="health-cottage"',
                       "Ei yhteyttä Villa Kuppisiin – näytetään viimeisin tieto",
                       "Mökki: turvalämpö", "Odottaa vahvistusta"):
            self.assertIn(needle, page)
        self.assertEqual(render_page(False), render_page(False))

    def test_pwa_routes_refuse_foreign_host(self):
        for path in ("/manifest.webmanifest", "/sw.js", "/icon-192.png", "/icon.svg"):
            status, _ = http_raw(self.server, "GET", path, [("Host", "evil.example")])
            self.assertEqual(status, 403, path)


class PwaPublicOriginTests(AppTestCase):
    ORIGIN = "https://pump.tail1234.ts.net"

    def test_public_host_needs_identity_like_the_page(self):
        service = PumpService(Path(self._tmp.name), client=FakeClient(make_unit_payloads()),
                              now=FakeClock(), **SERVICE_KWARGS)
        service.start()
        self._services.append(service)
        server = PumpAppServer(service, host="127.0.0.1", port=0, public_origin=self.ORIGIN)
        self._servers.append(server)
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1},
                         daemon=True).start()
        host = ("Host", "pump.tail1234.ts.net")
        for path in ("/", "/manifest.webmanifest", "/sw.js", "/icon-512.png"):
            status, _ = http_raw(server, "GET", path, [host])
            self.assertEqual(status, 403, path)
            status, _ = http_raw(server, "GET", path,
                                 [host, ("Tailscale-User-Login", "a@example.com")])
            self.assertEqual(status, 200, path)
            status, _ = http_raw(server, "GET", path,
                                 [host, ("Tailscale-User-Login", "a@example.com"),
                                  ("Origin", "https://evil.example")])
            self.assertEqual(status, 403, path)


if __name__ == "__main__":
    unittest.main()
