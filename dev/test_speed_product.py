#!/usr/bin/env python3
"""Product regression tests. Neutral fixtures only; no PikPak account."""
import io
import json
import os
import socket
import threading
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import call, patch
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "skills/pikpak-speed-guide/scripts/speed_diag.py"
spec = importlib.util.spec_from_file_location("speed_product", SCRIPT)
assert spec and spec.loader
speed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(speed)


class HeadRedirectHandler(BaseHTTPRequestHandler):
    target_hits = 0
    requests = []
    redirect_target = "/target"

    def log_message(self, *args):
        pass

    def do_HEAD(self):
        type(self).requests.append((self.command, self.path, dict(self.headers)))
        if self.path == "/target":
            type(self).target_hits += 1
            self.send_response(200)
        else:
            self.send_response(int(self.path.rsplit("/", 1)[-1]))
            self.send_header("Location", type(self).redirect_target)
            self.send_header("Content-Length", "262144")
        self.end_headers()

    def do_GET(self):
        type(self).requests.append((self.command, self.path, dict(self.headers)))
        type(self).target_hits += 1
        self.send_response(200)
        self.end_headers()


class TestHeadDestinationPolicy(unittest.TestCase):
    def test_invalid_timeout_is_rejected_before_dns_or_transport(self):
        destination = {"host": "127.0.0.1", "port": 9, "scheme": "http",
                       "endpoints": [(socket.AF_INET, socket.SOCK_STREAM,
                                      socket.IPPROTO_TCP, ("127.0.0.1", 9))]}
        for value in (0, -1, True, None, "1", float("nan"), float("inf"), 61):
            with self.subTest(timeout=repr(value)), \
                    patch.object(speed.transport, "resolve_destination", return_value=destination) as resolve, \
                    patch.object(speed.transport, "direct_opener", side_effect=RuntimeError("transport blocked")) as opener:
                result = speed.probe("http://127.0.0.1:9/", timeout=value)
                self.assertFalse(result["ok"], result)
                self.assertEqual(result["http_error"], "invalid_input")
                resolve.assert_not_called()
                opener.assert_not_called()

    def test_head_never_automatically_follows_redirects(self):
        HeadRedirectHandler.target_hits = 0
        HeadRedirectHandler.requests = []
        HeadRedirectHandler.redirect_target = "/target"
        server = ThreadingHTTPServer(("127.0.0.1", 0), HeadRedirectHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for code in (301, 302, 303, 307, 308):
                with self.subTest(status=code):
                    result = speed.probe(f"http://127.0.0.1:{server.server_port}/{code}", timeout=1)
                    self.assertFalse(result["ok"], result)
                    self.assertEqual(result["http_error"], "redirect_denied")
                    self.assertEqual(result["http_status"], code)
                    self.assertEqual(result["route"], "direct_no_proxy")
            self.assertEqual(HeadRedirectHandler.target_hits, 0)
            self.assertEqual([method for method, _, _ in HeadRedirectHandler.requests], ["HEAD"] * 5)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_proxy_settings_and_redirects_cannot_add_credentials_or_get(self):
        HeadRedirectHandler.target_hits = 0
        HeadRedirectHandler.requests = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), HeadRedirectHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        proxy_env = {key: "http://neutral-proxy.invalid:9" for key in
                     ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")}
        proxy_env.update(NO_PROXY="", no_proxy="")
        try:
            with patch.dict(os.environ, proxy_env), \
                    patch.object(speed.urllib.request, "getproxies", side_effect=AssertionError("must not read proxy settings")):
                for target in ("/target", "http://192.0.2.1/target", "https://neutral-target.invalid/"):
                    HeadRedirectHandler.redirect_target = target
                    result = speed.probe(f"http://127.0.0.1:{server.server_port}/302", timeout=1)
                    self.assertFalse(result["ok"], result)
                    self.assertEqual(result["http_error"], "redirect_denied")
            self.assertEqual(HeadRedirectHandler.target_hits, 0)
            self.assertEqual(len(HeadRedirectHandler.requests), 3)
            for method, path, headers in HeadRedirectHandler.requests:
                self.assertEqual((method, path), ("HEAD", "/302"))
                lowered = {key.lower() for key in headers}
                self.assertTrue(lowered.isdisjoint({"authorization", "proxy-authorization", "cookie"}))
        finally:
            HeadRedirectHandler.redirect_target = "/target"
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_http_errors_close_without_reading_or_echoing_response_body(self):
        class Body(io.BytesIO):
            def read(self, *args):
                raise AssertionError("diagnostic must not read an error body")

        destination = {"host": "neutral.example", "port": 443, "scheme": "https",
                       "endpoints": [(socket.AF_INET, socket.SOCK_STREAM,
                                      socket.IPPROTO_TCP, ("203.0.113.1", 443))]}
        for code in (301, 302, 303, 307, 308, 401, 403, 500):
            with self.subTest(status=code):
                body = Body(b"neutral-response-marker")
                error = speed.urllib.error.HTTPError("https://neutral.example/", code, "neutral", {}, body)
                with patch.object(speed.transport, "resolve_destination", return_value=destination), \
                        patch.object(speed.transport, "direct_opener") as opener:
                    opener.return_value.open.side_effect = error
                    result = speed.probe("https://neutral.example/", timeout=1)
                self.assertEqual(result["http_status"], code)
                self.assertTrue(body.closed)
                self.assertNotIn("neutral-response-marker", json.dumps(result))
                if code < 400:
                    self.assertFalse(result["ok"])
                    self.assertEqual(result["http_error"], "redirect_denied")
                else:
                    self.assertTrue(result["ok"])  # Reachability, not service health.

    def test_main_uses_exact_fixed_targets_without_account_or_url_inputs(self):
        expected = ["https://mypikpak.com", "https://dav.mypikpak.com", "https://api-drive.mypikpak.com"]
        self.assertEqual([url for _, url in speed.TARGETS], expected)
        output = io.StringIO()
        with patch.object(speed.sys, "argv", [str(SCRIPT), "--json"]), \
                patch.object(speed, "probe", return_value={"host": "neutral.example", "ok": True, "http_status": 200}) as probe, \
                redirect_stdout(output):
            speed.main()
        self.assertEqual(probe.call_args_list, [call(url) for url in expected])
        self.assertEqual(len(json.loads(output.getvalue())["results"]), 3)

    def test_head_hostname_to_private_is_denied_without_a_connection(self):
        records = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 443))]
        with patch.object(speed.socket, "getaddrinfo", return_value=records), \
                patch.object(speed.socket.socket, "connect", side_effect=AssertionError("no real connect")) as connect, \
                patch.object(speed.urllib.request, "getproxies", return_value={}):
            result = speed.probe("https://neutral.example/", timeout=1)
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["http_error"], "destination_denied")
        connect.assert_not_called()


class TestEvidenceBoundaries(unittest.TestCase):
    def test_head_latency_cannot_prove_throttling_or_throughput(self):
        tips = "\n".join(speed.recommend([
            {"host": "example.test", "ok": True, "total_ms": 3000},
        ]))
        self.assertIn("不能", tips)
        self.assertNotIn("典型的运营商 QoS", tips)
        self.assertNotIn("10-30MB/s", tips)
        self.assertNotIn("常能绕开", tips)


if __name__ == "__main__":
    unittest.main(verbosity=2)
