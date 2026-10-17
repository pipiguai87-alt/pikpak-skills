#!/usr/bin/env python3
"""Real loopback HTTP regression tests; no PikPak credentials or content."""
import os
from contextlib import redirect_stdout
import io
import socket
import ssl
from unittest.mock import patch
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "skills/pikpak-speed-guide/scripts/speed_bench.py"
DATA = bytes(range(256)) * 8192


class SyntheticSocket:
    """In-memory protocol peer; never creates an OS socket."""
    def __init__(self, peer="93.184.216.34"):
        self.peer = peer
        self.connected = []
        self.sent = []
        self.closed = False

    def settimeout(self, value):
        pass

    def setsockopt(self, *args):
        pass

    def connect(self, address):
        self.connected.append(address)

    def getpeername(self):
        return self.peer, 443

    def sendall(self, data):
        self.sent.append(data)

    def makefile(self, *args):
        return io.BytesIO(b"HTTP/1.1 206 Partial Content\r\nContent-Length: 4\r\nContent-Range: bytes 0-3/10\r\n\r\nabcd")

    def close(self):
        self.closed = True


class SampleHandler(BaseHTTPRequestHandler):
    seen_requests = []

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        type(self).seen_requests.append((self.path, self.headers.get("Range")))
        if self.path.startswith("/redirect-body/"):
            code = int(self.path.rsplit("/", 1)[1])
            self.send_response(code)
            self.send_header("Location", "/redirect-target")
            self.send_header("Content-Length", "262144")
            self.end_headers()
            try:
                self.wfile.write(b"neutral redirect body".ljust(262144, b"."))
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        if self.path == "/redirect-target":
            self.server.redirect_target_hits += 1
        if self.path.startswith("/forbidden"):
            self.send_error(403)
            return
        if self.path == "/slow":
            time.sleep(1)
        requested = self.headers.get("Range", "")
        if not requested.startswith("bytes=0-"):
            self.send_error(400)
            return
        size = min(int(requested.split("-")[1]) + 1, len(DATA))
        body = DATA[:size]
        if self.path.startswith("/different"):
            body = bytes((b + 1) % 256 for b in body)
        if self.path == "/short-body":
            body = body[:-1]
        elif self.path == "/long-body":
            body += b"."
        self.send_response(200 if self.path.startswith("/ignored") else 206)
        content_range = f"bytes 0-{size - 1}/{len(DATA)}"
        if self.path.startswith("/invalid-range"):
            content_range = f"bytes 1-{size}/{len(DATA)}"
        if self.path == "/unknown-total":
            content_range = f"bytes 0-{size - 1}/*"
        if self.path == "/gzip":
            import gzip
            body = gzip.compress(body)
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Range", content_range)
        length = str(size if self.path in ("/short-body", "/long-body") else len(body))
        if self.path == "/length-too-large":
            length = str(size + 1)
        elif self.path == "/length-too-small":
            length = str(size - 1)
        elif self.path == "/length-invalid":
            length = "not-a-number"
        if self.path not in ("/missing-length", "/chunked-only"):
            self.send_header("Content-Length", length)
        if self.path in ("/duplicate-length", "/conflicting-length"):
            self.send_header("Content-Length", str(size if self.path == "/duplicate-length" else size + 1))
        if self.path in ("/chunked-only", "/chunked-and-length"):
            self.send_header("Transfer-Encoding", "chunked")
            body = (f"{len(body):x}\r\n".encode() + body + b"\r\n0\r\n\r\n")
        self.end_headers()
        if self.path.startswith("/slow-body"):
            time.sleep(0.1)
        try:
            if self.path == "/drip":
                for byte in body:
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                    time.sleep(0.05)
            else:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


class TestBoundedSample(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), SampleHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def load_bench(self):
        self.assertTrue(SCRIPT.exists(), "缺少有限样本测速实现")
        spec = importlib.util.spec_from_file_location("speed_bench", SCRIPT)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_rejects_invalid_port_before_request(self):
        bench = self.load_bench()
        with self.assertRaises(ValueError):
            bench.validate_url("https://example.test:not-a-port/file")

    def test_hostname_to_non_global_addresses_never_connects(self):
        bench = self.load_bench()
        for address in ("127.0.0.1", "10.0.0.8", "169.254.1.2", "192.168.1.2",
                        "::1", "fd00::8", "fe80::8", "::ffff:127.0.0.1"):
            family = socket.AF_INET6 if ":" in address else socket.AF_INET
            endpoint = (address, 443, 0, 0) if family == socket.AF_INET6 else (address, 443)
            with self.subTest(address=address), \
                    patch.object(bench.socket, "getaddrinfo", return_value=[(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", endpoint)]), \
                    patch.object(bench.socket.socket, "connect", side_effect=AssertionError("no real connect")) as connect, \
                    patch.object(bench.urllib.request, "getproxies", return_value={}):
                result = bench._read_sample("https://neutral.example/sample", 4, 1)
            self.assertFalse(result["ok"], result)
            self.assertEqual(result["error"], "destination_denied")
            connect.assert_not_called()

    def test_connection_uses_one_pinned_resolution_not_rebound_dns_or_proxies(self):
        bench = self.load_bench()
        initial = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443))]
        rebound = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 443))]
        fake = SyntheticSocket()
        with patch.object(bench.socket, "getaddrinfo", side_effect=[initial, rebound]) as resolver, \
                patch.object(bench.socket, "socket", return_value=fake), \
                patch.object(ssl.SSLContext, "wrap_socket", return_value=fake) as tls, \
                patch.object(bench.urllib.request, "getproxies", return_value={}) as proxies:
            result = bench._read_sample("https://neutral.example/sample", 4, 1)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["route"], "direct_no_proxy")
        self.assertEqual(result["connected_ip"], "93.184.216.34")
        self.assertEqual(resolver.call_count, 1)
        self.assertEqual(fake.connected, [("93.184.216.34", 443)])
        self.assertEqual(tls.call_args.kwargs["server_hostname"], "neutral.example")
        proxies.assert_not_called()

    def test_actual_peer_mismatch_closes_before_tls_or_http(self):
        bench = self.load_bench()
        initial = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443))]
        fake = SyntheticSocket(peer="127.0.0.1")
        with patch.object(bench.socket, "getaddrinfo", return_value=initial), \
                patch.object(bench.socket, "socket", return_value=fake), \
                patch.object(ssl.SSLContext, "wrap_socket", return_value=fake) as tls, \
                patch.object(bench.urllib.request, "getproxies", return_value={}):
            result = bench._read_sample("https://neutral.example/sample", 4, 1)
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["error"], "destination_denied")
        self.assertEqual(fake.sent, [])
        self.assertTrue(fake.closed)
        tls.assert_not_called()

    def test_large_redirect_bodies_are_rejected_without_following(self):
        bench = self.load_bench()
        self.server.redirect_target_hits = 0
        for code in (301, 302, 303, 307, 308):
            with self.subTest(status=code):
                result = bench.measure(self.base + f"/redirect-body/{code}", sample_bytes=16, timeout=2)
                self.assertFalse(result["ok"], result)
                self.assertEqual(result["http_status"], code)
                self.assertEqual(result["error"], "redirect_denied")
                self.assertEqual(result["bytes_read"], 0)
                self.assertNotIn("mib_per_second", result)
        self.assertEqual(self.server.redirect_target_hits, 0)

    def test_http_failures_never_become_speed_results(self):
        bench = self.load_bench()
        for endpoint in ["/ignored", "/forbidden", "/invalid-range"]:
            with self.subTest(endpoint=endpoint):
                report = bench.measure(self.base + endpoint, sample_bytes=16384, timeout=2)
                self.assertFalse(report["ok"], report)
                self.assertNotIn("mib_per_second", report)

    def test_short_and_long_bodies_never_become_speed_or_comparison_results(self):
        bench = self.load_bench()
        valid = bench.measure(self.base + "/sample", sample_bytes=4, timeout=2)
        self.assertTrue(valid["ok"], valid)
        for path, read_bytes in (("/short-body", 3), ("/long-body", 5)):
            with self.subTest(path=path):
                result = bench.measure(self.base + path, sample_bytes=4, timeout=2)
                self.assertFalse(result["ok"], result)
                self.assertEqual(result["error"], "sample_length_mismatch")
                self.assertEqual(result["bytes_read"], read_bytes)
                self.assertNotIn("mib_per_second", result)
                self.assertFalse(bench.compare([valid], [result])["comparable"])

    def test_single_range_requires_unambiguous_fixed_length_framing(self):
        bench = self.load_bench()
        for path in ("/length-too-large", "/length-too-small", "/length-invalid", "/duplicate-length",
                     "/conflicting-length", "/missing-length", "/chunked-only", "/chunked-and-length"):
            with self.subTest(path=path):
                result = bench.measure(self.base + path, sample_bytes=4, timeout=2)
                self.assertFalse(result["ok"], result)
                self.assertEqual(result["error"], "invalid_framing")
                self.assertEqual(result["bytes_read"], 0)
                self.assertNotIn("mib_per_second", result)

    def test_duplicate_range_or_encoding_headers_never_become_samples(self):
        from email.message import Message
        from unittest.mock import MagicMock
        bench = self.load_bench()
        valid = {"ok": True, "bytes_read": 4, "total_bytes": 10, "sample_sha256": "a" * 64,
                 "mib_per_second": 1.0, "ttfb_ms": 2.0, "elapsed_seconds": 0.1}
        cases = (("Content-Range", "bytes 0-3/10", "invalid_content_range"),
                 ("Content-Range", "bytes 1-4/10", "invalid_content_range"),
                 ("Content-Encoding", "identity", "unexpected_encoding"),
                 ("Content-Encoding", "gzip", "unexpected_encoding"))
        for field, duplicate, error in cases:
            headers = Message()
            headers["Content-Range"] = "bytes 0-3/10"
            headers["Content-Encoding"] = "identity"
            headers["Content-Length"] = "4"
            headers[field] = duplicate
            response = MagicMock(status=206, headers=headers)
            response.fp = io.BytesIO(b"abcd")
            opener = MagicMock()
            opener.open.return_value.__enter__.return_value = response
            with self.subTest(field=field, duplicate=duplicate), \
                    patch.object(bench, "resolve_destination", return_value={"connected_ip": "127.0.0.1"}), \
                    patch.object(bench, "direct_opener", return_value=opener):
                result = bench._read_sample("http://127.0.0.1/sample", 4, 1)
            self.assertFalse(result["ok"], result)
            self.assertEqual(result["error"], error)
            self.assertEqual(result["bytes_read"], 0)
            self.assertNotIn("mib_per_second", result)
            self.assertEqual(response.fp.tell(), 0)
            self.assertFalse(bench.compare([valid], [result])["comparable"])

    def test_compressed_or_unknown_total_samples_are_not_comparable(self):
        bench = self.load_bench()
        for path, error in (("/gzip", "unexpected_encoding"), ("/unknown-total", "invalid_content_range")):
            with self.subTest(path=path):
                result = bench.measure(self.base + path, sample_bytes=4, timeout=2)
                self.assertFalse(result["ok"], result)
                self.assertEqual(result["error"], error)
                self.assertEqual(result["bytes_read"], 0)
                self.assertNotIn("mib_per_second", result)
                self.assertFalse(bench.compare([result], [result])["comparable"])

    def test_redirect_handler_closes_without_any_body_read(self):
        from unittest.mock import Mock
        bench = self.load_bench()
        fp = Mock()
        fp.read.side_effect = AssertionError("redirect body must not be drained")
        request = urllib.request.Request("https://neutral.example/sample")
        with self.assertRaises(bench.urllib.error.HTTPError):
            bench.SafeRedirect().http_error_302(request, fp, 302, "redirect", {"Location": "/sample"})
        fp.close.assert_called_once_with()
        fp.read.assert_not_called()

    def test_rejected_redirect_error_owns_an_already_closed_stream(self):
        bench = self.load_bench()
        body = io.BytesIO(b"neutral redirect body")
        request = urllib.request.Request("https://neutral.example/sample")
        with self.assertRaises(bench.urllib.error.HTTPError) as stopped:
            bench.SafeRedirect().http_error_302(request, body, 302, "redirect", {"Location": "/sample"})
        self.assertTrue(body.closed)
        self.assertTrue(getattr(stopped.exception, "closed", False))

    def test_redirects_never_copy_synthetic_auth_or_consult_proxy_environment(self):
        from unittest.mock import Mock
        bench = self.load_bench()
        request = urllib.request.Request("https://one.example.test/sample", headers={
            "Proxy-Authorization": "synthetic-proxy-marker", "Authorization": "synthetic-origin-marker"})
        with patch.object(bench.urllib.request, "getproxies", side_effect=AssertionError("must not read proxy settings")), \
                patch.object(bench.urllib.request, "proxy_bypass", side_effect=AssertionError("no proxy bypass lookup")), \
                patch.object(bench.socket, "getaddrinfo", side_effect=AssertionError("zero real DNS")), \
                patch.object(bench.socket, "socket", side_effect=AssertionError("zero real socket")):
            handler = bench.SafeRedirect()
            opener = bench.direct_opener({"host": "one.example.test", "endpoints": []})
            self.assertIsNotNone(opener)
            for target in ("https://two.example.test/sample", "http://127.0.0.1/next", "https://one.example.test/loop"):
                with self.subTest(target=target), self.assertRaises(ValueError):
                    handler.redirect_request(request, None, 302, "redirect", {}, target)
                for code in (301, 302, 303, 307, 308):
                    body = Mock()
                    body.read.side_effect = AssertionError("no redirect body drain")
                    with self.subTest(target=target, code=code), self.assertRaises(bench.urllib.error.HTTPError):
                        getattr(handler, "http_error_" + str(code))(request, body, code, "redirect", {"Location": target})
                    body.close.assert_called_once_with()
                    body.read.assert_not_called()

    def test_direct_worker_revalidates_limits_before_any_http(self):
        before = len(SampleHandler.seen_requests)
        for size, timeout in ((16777217, 1), (4, 120), (True, 1), (4, float("nan")), (0, 1), (4, 0)):
            with self.subTest(size=size, timeout=timeout):
                proc = subprocess.run([sys.executable, str(SCRIPT), "--worker"],
                    input=json.dumps({"url": self.base + "/forbidden", "sample_bytes": size, "timeout": timeout}),
                    capture_output=True, text=True, timeout=2)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(json.loads(proc.stdout)["error"], "invalid_input")
        self.assertEqual(len(SampleHandler.seen_requests), before)

    def test_direct_worker_has_own_deadline_against_continuous_drip(self):
        started = time.monotonic()
        proc = subprocess.run([sys.executable, str(SCRIPT), "--worker"],
            input=json.dumps({"url": self.base + "/drip", "sample_bytes": 16, "timeout": 0.15}),
            capture_output=True, text=True, timeout=2)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["error"], "deadline_exceeded")
        self.assertLess(time.monotonic() - started, 0.8)

    def test_worker_has_a_bounded_deadline(self):
        bench = self.load_bench()
        started = time.monotonic()
        result = bench.measure(self.base + "/slow", sample_bytes=16384, timeout=0.1)
        self.assertFalse(result["ok"], result)
        self.assertLess(time.monotonic() - started, 1.5)

    def test_ttfb_includes_wait_for_first_body_byte(self):
        bench = self.load_bench()
        result = bench.measure(self.base + "/slow-body", sample_bytes=16384, timeout=2)
        self.assertTrue(result["ok"], result)
        self.assertGreaterEqual(result["ttfb_ms"], 60)

    def test_invalid_sources_are_rejected_without_echoing_secrets(self):
        bench = self.load_bench()
        for source in ["file:///etc/hosts", "http://example.test/file", "https://user:fixture-private@example.test/file"]:
            with self.subTest(source=source):
                result = bench.measure(source, sample_bytes=16384, timeout=2)
                self.assertFalse(result["ok"], result)
                self.assertNotIn("fixture-private", json.dumps(result))

    def test_cli_rejects_nonprivate_url_files_and_symlinks_before_measurement(self):
        bench = self.load_bench()
        with tempfile.TemporaryDirectory() as folder:
            plain = Path(folder) / "neutral.url"
            plain.write_text(self.base + "/sample?synthetic=fixture-private")
            link = Path(folder) / "link.url"
            link.symlink_to(plain)
            for source, mode in ((plain, 0o644), (plain, 0o660), (link, 0o600)):
                plain.chmod(mode)
                out = io.StringIO()
                with self.subTest(mode=mode, symlink=source == link), \
                        patch.object(sys, "argv", [str(SCRIPT), "--baseline-url-file", str(source), "--json"]), \
                        patch.object(bench, "measure", side_effect=AssertionError("URL file must be rejected before HTTP")), \
                        redirect_stdout(out):
                    status = bench.main()
                    self.assertEqual(status, 2)
                    self.assertEqual(json.loads(out.getvalue())["error"], "invalid_url_file")
                    self.assertNotIn("fixture-private", out.getvalue())

    def test_fifo_url_input_is_rejected_without_a_blocking_open(self):
        bench = self.load_bench()
        with tempfile.TemporaryDirectory() as folder:
            fifo = Path(folder) / "neutral.fifo"
            os.mkfifo(fifo, 0o600)
            out = io.StringIO()
            started = time.monotonic()
            with patch.object(sys, "argv", [str(SCRIPT), "--baseline-url-file", str(fifo), "--timeout", "0.1", "--json"]), \
                    patch("builtins.open", side_effect=AssertionError("ordinary open on FIFO would block")), \
                    patch.object(bench, "measure", side_effect=AssertionError("no HTTP")), redirect_stdout(out):
                status = bench.main()
            self.assertEqual(status, 2)
            self.assertEqual(json.loads(out.getvalue())["error"], "invalid_url_file")
            self.assertLess(time.monotonic() - started, 0.5)

    def test_cli_measures_same_sample_using_private_url_files(self):
        with tempfile.TemporaryDirectory() as temp:
            baseline = Path(temp) / "baseline.url"
            candidate = Path(temp) / "candidate.url"
            baseline.write_text(self.base + "/sample?synthetic=fixture-private", encoding="utf-8")
            candidate.write_text(self.base + "/sample", encoding="utf-8")
            baseline.chmod(0o600)
            candidate.chmod(0o600)
            proc = subprocess.run([sys.executable, str(SCRIPT),
                "--baseline-url-file", str(baseline), "--candidate-url-file", str(candidate),
                "--bytes", "16384", "--repeat", "2", "--timeout", "2", "--json"],
                text=True, capture_output=True, timeout=15)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual(result["route"], "direct_no_proxy")
        self.assertTrue(result["comparison"]["comparable"], result)
        self.assertEqual(len(result["baseline"]), 2)
        self.assertEqual(len(result["candidate"]), 2)
        self.assertNotIn("fixture-private", proc.stdout + proc.stderr)
        self.assertNotIn(self.base, proc.stdout + proc.stderr)

    def test_zero_port_is_rejected_instead_of_using_a_default_destination(self):
        bench = self.load_bench()
        for url in ("https://neutral.example:0/sample", "http://127.0.0.1:0/sample"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                bench.validate_url(url)

    def test_remote_redirect_cannot_contact_loopback_or_downgrade(self):
        bench = self.load_bench()
        request = urllib.request.Request("https://example.test/sample")
        for destination in [self.base + "/sample", "http://example.test/sample"]:
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                bench.SafeRedirect().redirect_request(request, None, 302, "redirect", {}, destination)

    def test_compare_rejects_missing_and_nonfinite_metrics_without_raising(self):
        bench = self.load_bench()
        valid = {"ok": True, "bytes_read": 4, "total_bytes": 10, "sample_sha256": "a" * 64,
                 "mib_per_second": 1.0, "ttfb_ms": 2.0, "elapsed_seconds": 0.1}
        invalid = []
        for key in ("bytes_read", "total_bytes", "sample_sha256", "mib_per_second", "ttfb_ms", "elapsed_seconds"):
            missing = dict(valid)
            missing.pop(key)
            invalid.append(missing)
        for value in (0, -1, True, "1", float("nan"), float("inf"), float("-inf")):
            invalid.append(dict(valid, mib_per_second=value))
        for value in (-1, True, "2", float("nan"), float("inf")):
            invalid.append(dict(valid, ttfb_ms=value))
        invalid += [dict(valid, bytes_read=True), dict(valid, total_bytes=3), dict(valid, sample_sha256="not-a-hash")]
        for record in invalid:
            with self.subTest(record=record):
                result = bench.compare([valid], [record])
                self.assertFalse(result["comparable"], result)
                self.assertEqual(result["reason"], "invalid_measurement")
        for before, after in ((1e-308, 1e308), (1e308, 1e-308)):
            with self.subTest(before=before, after=after):
                result = bench.compare([dict(valid, mib_per_second=before)], [dict(valid, mib_per_second=after)])
                self.assertFalse(result["comparable"], result)

    def test_compare_medians_for_odd_and_even_repeats(self):
        bench = self.load_bench()
        valid = {"ok": True, "bytes_read": 4, "total_bytes": 10, "sample_sha256": "a" * 64,
                 "mib_per_second": 1.0, "ttfb_ms": 2.0, "elapsed_seconds": 0.1}
        odd = [dict(valid, mib_per_second=value) for value in (1, 3, 8)]
        even = [dict(valid, mib_per_second=value) for value in (2, 6, 8, 10)]
        result = bench.compare(odd, even)
        self.assertTrue(result["comparable"], result)
        self.assertEqual(result["baseline_median_mib_s"], 3)
        self.assertEqual(result["candidate_median_mib_s"], 7)
        self.assertAlmostEqual(result["throughput_ratio"], 7 / 3)

    def test_comparison_requires_matching_samples(self):
        bench = self.load_bench()
        self.assertTrue(callable(getattr(bench, "compare", None)), "缺少完整性门禁的 A/B 对比")
        first = bench.measure(self.base + "/sample", sample_bytes=16384, timeout=2)
        same = bench.measure(self.base + "/sample", sample_bytes=16384, timeout=2)
        different = bench.measure(self.base + "/different", sample_bytes=16384, timeout=2)
        good = bench.compare([first], [same])
        self.assertTrue(good["comparable"], good)
        self.assertEqual(good["scope"], "prefix_sample_only")
        self.assertGreater(good["throughput_ratio"], 0)
        self.assertFalse(bench.compare([first], [different])["comparable"])
        self.assertFalse(bench.compare([first], [{"ok": False}])["comparable"])
        self.assertFalse(bench.compare([], [same])["comparable"])

    def test_valid_range_measures_real_bytes_without_leaking_url(self):
        bench = self.load_bench()
        report = bench.measure(self.base + "/sample?synthetic=fixture-private", sample_bytes=16384, timeout=2)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["bytes_read"], 16384)
        self.assertEqual(report["sample_sha256"], hashlib.sha256(DATA[:16384]).hexdigest())
        self.assertEqual(report["http_status"], 206)
        self.assertGreater(report["elapsed_seconds"], 0)
        self.assertGreater(report["mib_per_second"], 0)
        self.assertNotIn("fixture-private", json.dumps(report))
        self.assertNotIn(self.base, json.dumps(report))


if __name__ == "__main__":
    unittest.main(verbosity=2)
