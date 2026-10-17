"""WebDAV preflight regressions: neutral in-memory metadata only."""
from __future__ import annotations

import importlib.util
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "webdav_access_test", ROOT / "skills/pikpak-webdav-guide/scripts/webdav_check.py"
)
assert SPEC and SPEC.loader
WD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WD)


class TestWebdavAccess(unittest.TestCase):
    def run_metadata(self, data):
        out = io.StringIO()
        with patch.object(WD, "pikpak_json", return_value=(data, None)), \
                patch.object(sys, "argv", ["webdav_check.py", "--json", "--account-owner-confirmed"]), \
                redirect_stdout(out):
            WD.main()
        return json.loads(out.getvalue())

    def test_no_arguments_never_query_an_account(self):
        with patch.object(WD, "pikpak_json", return_value=({"logged_in": False}, None)) as cli, \
                patch.object(sys, "argv", ["webdav_check.py", "--json"]), \
                redirect_stdout(io.StringIO()), patch.object(sys, "stderr", io.StringIO()):
            with self.assertRaises(SystemExit) as stopped:
                WD.main()
        self.assertEqual(stopped.exception.code, 2)
        cli.assert_not_called()

    def test_fixture_mode_never_uses_a_cli_or_network(self):
        with tempfile.TemporaryDirectory(prefix="pikpak-webdav-fixture-") as tmp:
            fixture = Path(tmp) / "metadata.json"
            fixture.write_text(json.dumps({"logged_in": True, "vip": {"type": "unknown"}}))
            out = io.StringIO()
            with patch.object(WD, "pikpak_json", side_effect=AssertionError("no account calls")), \
                    patch.object(WD.subprocess, "run", side_effect=AssertionError("no subprocess")), \
                    patch.object(sys, "argv", ["webdav_check.py", "--json", "--from-fixture", str(fixture)]), \
                    redirect_stdout(out):
                WD.main()
            result = json.loads(out.getvalue())
        self.assertEqual(result["source"], "fixture")
        self.assertIsNone(result["can_use_webdav"])
        self.assertEqual(result["membership"]["state"], "unknown")
        self.assertEqual(result["transfer_quota"]["state"], "unknown")

    def test_malformed_metadata_is_rejected_without_traceback(self):
        invalid = [[], {"logged_in": "false"}, {"logged_in": True, "vip": ["not-a-mapping"]},
                   {"logged_in": True, "vip": {"type": 5}}, {"logged_in": True, "storage": [1]}]
        for data in invalid:
            with self.subTest(data=data), patch.object(sys, "stderr", io.StringIO()):
                with self.assertRaises(SystemExit) as stopped:
                    self.run_metadata(data)
                self.assertEqual(stopped.exception.code, 1)

    def test_main_missing_or_null_login_remains_unknown_without_login_advice(self):
        for data in ({}, {"logged_in": None}):
            with self.subTest(data=data):
                result = self.run_metadata(data)
                self.assertIsNone(result["logged_in"])
                self.assertIsNone(result["can_use_webdav"])
                self.assertEqual(result["membership"]["state"], "unknown")
                self.assertEqual(result["webdav_connection"], "not_tested")
                checks = "\n".join(result["checks"])
                self.assertIn("未知", checks)
                self.assertNotIn("未登录", checks)
                self.assertNotIn("auth login", checks)
                output = io.StringIO()
                with redirect_stdout(output):
                    WD.emit(result, False)
                self.assertIn("登录状态：未知", output.getvalue())
                self.assertNotIn("auth login", output.getvalue())

    def test_unknown_is_not_rendered_as_denied(self):
        out = io.StringIO()
        with redirect_stdout(out):
            WD.emit({"logged_in": None, "can_use_webdav": None, "checks": []}, False)
        self.assertIn("可用 WebDAV：未验证", out.getvalue())
        self.assertIn("登录状态：未知", out.getvalue())

    def test_cli_errors_do_not_repeat_raw_sensitive_output(self):
        response = WD.subprocess.CompletedProcess(["neutral-cli"], 1, "", "url=https://example.invalid/?signature=SYNTHETIC_PRIVATE_MARKER")
        with patch.object(WD.subprocess, "run", return_value=response):
            data, err = WD.pikpak_json("auth", "status")
        self.assertIsNone(data)
        self.assertNotIn("SYNTHETIC_PRIVATE_MARKER", err)
        self.assertNotIn("https://", err)
        self.assertIn("1", err)

    def test_storage_fields_never_export_nested_or_arbitrary_values(self):
        marker = "NEUTRAL_STORAGE_BOUNDARY_SENTINEL"
        invalid = [{"token": marker}, [{"pass_code": marker}], True, False, -1,
                   1.5, float("nan"), float("inf"), "https://example.invalid/?token=" + marker,
                   "1 TB " + marker, marker, "1" + "0" * 400, 10 ** 400]
        for field in ("total", "used", "in_trash"):
            for value in invalid:
                with self.subTest(field=field, value_type=type(value).__name__):
                    result = self.run_metadata({"logged_in": True, "storage": {field: value}})
                    self.assertIsNone(result["storage"][field])
                    self.assertNotIn(marker, json.dumps(result))
                    output = io.StringIO()
                    with redirect_stdout(output):
                        WD.emit(result, False)
                    self.assertNotIn(marker, output.getvalue())
        valid = {"total": "10 TB", "used": 1024, "in_trash": "0 B"}
        result = self.run_metadata({"logged_in": True, "storage": valid})
        self.assertEqual(result["storage"], valid)
        self.assertEqual(result["transfer_quota"]["state"], "unknown")

    def test_empty_fixture_path_is_an_offline_error_before_cli(self):
        with patch.object(WD, "pikpak_json", side_effect=AssertionError("no account calls")) as cli, \
                patch.object(WD.subprocess, "run", side_effect=AssertionError("no subprocess")), \
                patch.object(sys, "argv", ["webdav_check.py", "--from-fixture", "", "--json"]), \
                redirect_stdout(io.StringIO()), patch.object(sys, "stderr", io.StringIO()):
            with self.assertRaises(SystemExit) as stopped:
                WD.main()
        self.assertEqual(stopped.exception.code, 1)
        cli.assert_not_called()

    def test_access_references_separate_releases_from_researched_snapshots(self):
        for skill in ("pikpak-speed-guide", "pikpak-webdav-guide"):
            with self.subTest(skill=skill):
                text = (ROOT / "skills" / skill / "references/upstream-design.md").read_text()
                self.assertIn("https://github.com/rclone/rclone/releases/tag/v1.75.2", text)
                self.assertIn("2026-10-09T11:03:19Z", text)
                self.assertIn("snapshot_ancestry=not_verified", text)
                self.assertIn("proxy_release=not_verified", text)
                self.assertIn("不代表运行", text)
                self.assertNotIn("/Users/", text)

    def test_vip_metadata_is_whitelisted_normalized_and_never_echoes_unknown_strings(self):
        marker = "NEUTRAL_VIP_BOUNDARY_SENTINEL"
        valid = {"type": "Platinum", "status": "ok", "expire_time": "2099-01-01T00:00:00Z"}
        invalid = (marker, "https://example.invalid/?token=" + marker,
                   "token=" + marker, "x" * 5000)
        for field in ("type", "status", "expire_time"):
            for value in invalid:
                with self.subTest(field=field, value_length=len(value)):
                    result = self.run_metadata({"logged_in": True, "vip": dict(valid, **{field: value})})
                    self.assertNotIn(marker, json.dumps(result))
                    self.assertNotIn(value, json.dumps(result))
                    self.assertEqual(result["membership"]["state"], "unknown")
                    self.assertIsNone(result["can_use_webdav"])
        valid_result = self.run_metadata({"logged_in": True, "vip": valid})
        self.assertEqual(valid_result["vip"], {
            "type": "platinum", "status": "ok", "expire_time": "2099-01-01T00:00:00+00:00"})
        self.assertEqual(valid_result["membership"]["state"], "reported_current")
        for expiry in ("2099-01-01T00:00:00", "2099-99-99T00:00:00Z"):
            result = self.run_metadata({"logged_in": True, "vip": dict(valid, expire_time=expiry)})
            self.assertEqual(result["vip"]["expire_time"], "")
            self.assertEqual(result["membership"]["state"], "unknown")

    def test_expired_membership_does_not_grant_webdav(self):
        result = self.run_metadata({
            "logged_in": True,
            "vip": {"type": "Platinum", "status": "ok", "expire_time": "2000-01-01T00:00:00Z"},
            "storage": {"total": "10 TB", "used": "1 TB"},
        })
        self.assertIsNot(result["can_use_webdav"], True)
        self.assertEqual(result["membership"]["state"], "reported_expired")


if __name__ == "__main__":
    unittest.main(verbosity=2)
