#!/usr/bin/env python3
"""中性 fixture/mock 回归；独立运行，不调用真实 PikPak。"""
import importlib.util
import hashlib
import io
import json
import os
import shlex
import socket
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def setUpModule():
    # Both unittest discovery and this file's __main__ use the same guard.
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from dev.run_tests import offline_environment
    stack = ExitStack()
    unittest.addModuleCleanup(stack.close)
    stack.enter_context(offline_environment())
    stack.enter_context(patch.dict(os.environ, {"PIKPAK_TIMEOUT": "90"}))
    for owner, names in (
        (subprocess, ("run", "Popen", "call", "check_call", "check_output")),
        (socket.socket, ("connect", "connect_ex", "sendto")),
        (socket, ("create_connection", "getaddrinfo")),
        (urllib.request, ("urlopen",)),
        (urllib.request.OpenerDirector, ("open",)),
        (os, ("system", "popen")),
    ):
        for name in names:
            stack.enter_context(patch.object(owner, name, side_effect=AssertionError("offline cleanup tests deny processes/network/notifications")))


def load(skill, filename):
    spec = importlib.util.spec_from_file_location(filename, ROOT / "skills" / skill / "scripts" / (filename + ".py"))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ADS = load("pikpak-ad-cleaner", "scan_ads")
DUP = load("pikpak-dedup", "analyze_dupes")
STORAGE = load("pikpak-storage-report", "storage_report")
MODULES = (ADS, DUP, STORAGE)


def item(fid="neutral1", name="neutral.url", size: object="200000", **extra):
    return {"id": fid, "kind": "drive#file", "name": name, "size": size, **extra}


def folder(fid, name="neutral-folder"):
    return {"id": fid, "kind": "drive#folder", "name": name}


def fixture(files, token="", more=None):
    return {"": {"": {"files": files, "next_page_token": token}, **(more or {})}}


def scan(mod, tree, limit=50000):
    counts = {"files": 0, "folders": 0}
    if mod is STORAGE:
        return mod.walk_tree("", tree, "root", counts, limit), counts
    out = []
    mod.walk("", tree, [], out, counts, limit)
    return out, counts


class OfflineInputs(unittest.TestCase):
    def test_explicit_null_and_nonobject_fixtures_never_fall_back_to_cli(self):
        for mod in MODULES:
            for root in (None, [], "neutral", 0, False):
                with self.subTest(module=mod.__name__, root=root), tempfile.TemporaryDirectory() as tmp:
                    source = Path(tmp) / "neutral.json"
                    source.write_text(json.dumps(root))
                    out, err = io.StringIO(), io.StringIO()
                    fake = SimpleNamespace(returncode=0, stdout='{"files": []}', stderr="")
                    code = 0
                    with patch.object(sys, "argv", ["script", "--from-fixture", str(source), "--json", "-"]), \
                            patch.object(subprocess, "run", return_value=fake) as cli, \
                            redirect_stdout(out), redirect_stderr(err):
                        try:
                            mod.main()
                        except SystemExit as stopped:
                            code = stopped.code
                    cli.assert_not_called()
                    self.assertEqual(code, 2, err.getvalue())
                    report = json.loads(out.getvalue())
                    self.assertIs(report["complete"], False)
                    self.assertEqual(report["source"], "fixture")
                    self.assertNotIn("pikpak rm ", out.getvalue())


class SafeDiagnostics(unittest.TestCase):
    def test_cli_errors_never_echo_raw_streams_to_json_stderr_or_report_file(self):
        raw = ("Authorization: Bearer neutral-bearer-sentinel\n"
               "Cookie: session=neutral-cookie-sentinel password=neutral-password-sentinel\n"
               "https://neutral-user-sentinel:neutral-pass-sentinel@example.test/f?"
               "token=neutral-query-sentinel&signature=neutral-signature-sentinel")
        for mod in MODULES:
            for stream in ("stderr", "stdout"):
                for destination in ("-", "file"):
                    with self.subTest(module=mod.__name__, stream=stream, destination=destination), tempfile.TemporaryDirectory() as tmp:
                        target = Path(tmp) / "report.json"
                        target.write_text('{"complete":true,"stale":true}')
                        out, err = io.StringIO(), io.StringIO()
                        fake = SimpleNamespace(returncode=23, stderr=raw if stream == "stderr" else "", stdout=raw if stream == "stdout" else "")
                        with patch.object(sys, "argv", ["script", "--json", "-" if destination == "-" else str(target)]), \
                                patch.object(subprocess, "run", return_value=fake) as cli, \
                                redirect_stdout(out), redirect_stderr(err):
                            with self.assertRaises(SystemExit) as stopped:
                                mod.main()
                        self.assertEqual(stopped.exception.code, 2)
                        cli.assert_called_once()
                        emitted = out.getvalue() if destination == "-" else target.read_text()
                        self.assertNotIn("sentinel", out.getvalue() + err.getvalue() + emitted)
                        report = json.loads(emitted)
                        self.assertIs(report["complete"], False)
                        self.assertEqual(report["error_code"], "CLI_FAILED")
                        self.assertEqual(report["cli_exit_code"], 23)
                        self.assertNotIn("stale", report)


class ScanIntegrity(unittest.TestCase):
    def test_repeated_file_ids_do_not_inflate_counts_or_bytes(self):
        tree = fixture([item()], "p2", {"p2": {"files": [item()], "next_page_token": ""}})
        for mod in MODULES:
            with self.subTest(module=mod.__name__), patch.object(mod.subprocess, "run", side_effect=AssertionError("no real CLI")):
                result, counts = scan(mod, tree)
                self.assertEqual(counts["files"], 1)
                if mod is STORAGE:
                    self.assertEqual(result.size, 200000)
                    self.assertEqual(result.file_count, 1)
                else:
                    self.assertEqual(len(result), 1)


class IdentitySafety(unittest.TestCase):
    def test_conflicting_ids_fail_closed_in_either_page_order_and_cannot_be_approved(self):
        first = item("same", "neutral.url", hash="neutral-hash-a", params={"width": "1920"},
                     modified_time="2026-01-01T00:00:00Z", created_time="2025-01-01T00:00:00Z")
        changes = ({"name": "neutral.srt"}, {"kind": "drive#folder"}, {"size": "300000"},
                   {"hash": "neutral-hash-b"}, {"params": {"width": "3840"}},
                   {"modified_time": "2026-02-01T00:00:00Z"},
                   {"created_time": "2025-02-01T00:00:00Z"})
        for mod in MODULES:
            for change in changes:
                for reverse in (False, True):
                    with self.subTest(module=mod.__name__, change=change, reverse=reverse), tempfile.TemporaryDirectory() as tmp:
                        pair = [first, {**first, **change}]
                        if reverse:
                            pair.reverse()
                        tree = fixture([pair[0]], "p2", {"p2": {"files": [pair[1]]}})
                        tree["same"] = {"": {"files": [item("child")]}}
                        source = Path(tmp) / "fixture.json"
                        source.write_text(json.dumps(tree))
                        code, out, err = invoke_main(mod, ["--from-fixture", str(source), "--json", "-"])
                        self.assertEqual(code, 2, err)
                        report = json.loads(out)
                        self.assertIs(report["complete"], False)
                        self.assertEqual(report["error_code"], "IDENTITY_CONFLICT")
                        self.assertIs(report["identity_conflict"], True)
                        self.assertNotIn("high", report)
                        self.assertNotIn("groups", report)
                        self.assertNotIn("tree", report)
                        self.assertNotIn("pikpak rm ", out)
                        if mod in (ADS, DUP):
                            frozen = Path(tmp) / "frozen.json"
                            raw = out.encode()
                            frozen.write_bytes(raw)
                            argv = ["--from-report", str(frozen), "--report-sha256", hashlib.sha256(raw).hexdigest(),
                                    "--approve-id", "same", "--emit-trash-cmd"]
                            if mod is ADS:
                                argv.append("high")
                            code, commands, _ = invoke_main(mod, argv)
                            self.assertEqual(code, 2)
                            self.assertEqual(commands, "")


class ScanBoundaries(unittest.TestCase):
    def test_repeated_page_token_stops_before_third_fetch(self):
        tree = fixture([], "again", {"again": {"files": [], "next_page_token": "again"}})
        for mod in MODULES:
            original = mod.ls_page
            calls = []
            def bounded(*args):
                calls.append(args[2])
                if len(calls) > 3:
                    self.fail("分页 token 循环未被阻断")
                return original(*args)
            with self.subTest(module=mod.__name__), patch.object(mod, "ls_page", bounded):
                with self.assertRaises(RuntimeError):
                    scan(mod, tree)
                self.assertEqual(len(calls), 2)

    def test_folder_cycle_is_not_recursed(self):
        tree = fixture([folder("A")])
        tree["A"] = {"": {"files": [folder("A")], "next_page_token": ""}}
        for mod in MODULES:
            original = mod.ls_page
            calls = []
            def bounded(*args):
                calls.append(args[0])
                if len(calls) > 3:
                    self.fail("目录环未被阻断")
                return original(*args)
            with self.subTest(module=mod.__name__), patch.object(mod, "ls_page", bounded):
                with self.assertRaises(RuntimeError):
                    scan(mod, tree)
                self.assertEqual(calls, ["", "A"])

    def test_missing_fixture_page_and_error_json_are_not_empty_success(self):
        for mod in MODULES:
            for tree in ({}, {"": {"": {"error": "fixture-denied"}}}):
                with self.subTest(module=mod.__name__, tree=tree):
                    with self.assertRaises(RuntimeError):
                        scan(mod, tree)

    def test_limit_and_cli_failure_write_explicit_incomplete_report(self):
        for mod in MODULES:
            for failure in ("limit", "cli"):
                with self.subTest(module=mod.__name__, failure=failure), tempfile.TemporaryDirectory() as tmp:
                    source, target = Path(tmp) / "fixture.json", Path(tmp) / "report.json"
                    source.write_text(json.dumps(fixture([item("a"), item("b")])))
                    target.write_text('{"complete": true, "stale": true}')
                    argv = ["script", "--json", str(target), "--max-items", "1"]
                    if failure == "limit":
                        argv += ["--from-fixture", str(source)]
                    fake = SimpleNamespace(returncode=1, stderr="fixture CLI denied", stdout="")
                    out, err = io.StringIO(), io.StringIO()
                    with patch.object(sys, "argv", argv), patch.object(mod.subprocess, "run", return_value=fake), redirect_stdout(out), redirect_stderr(err):
                        with self.assertRaises(SystemExit) as stopped:
                            mod.main()
                    self.assertEqual(stopped.exception.code, 2)
                    report = json.loads(target.read_text())
                    self.assertIs(report["complete"], False)
                    self.assertEqual(report["truncated"], failure == "limit")
                    self.assertTrue(report["errors"])
                    self.assertNotIn("stale", report)
                    self.assertNotIn("pikpak rm ", out.getvalue())
                    self.assertNotIn("扫描完成", out.getvalue())


def run_fixture_main(mod, tree, extra=()):
    with tempfile.TemporaryDirectory() as tmp:
        source, target = Path(tmp) / "fixture.json", Path(tmp) / "report.json"
        source.write_text(json.dumps(tree))
        out, err = io.StringIO(), io.StringIO()
        argv = ["script", "--from-fixture", str(source), "--json", str(target), *extra]
        with patch.object(sys, "argv", argv), patch.object(mod.subprocess, "run", side_effect=AssertionError("no real CLI")), redirect_stdout(out), redirect_stderr(err):
            mod.main()
        return json.loads(target.read_text()), out.getvalue()


class CandidateSafety(unittest.TestCase):
    def test_name_match_is_not_exact_even_if_opaque_hashes_match(self):
        for sizes in (("200000", "300000"), ("200000", "200000")):
            tree = fixture([item("a", "Neutral.Movie.1080p.mkv", sizes[0], hash="opaque-cloud-hash"),
                            item("b", "Neutral.Movie.2160p.mkv", sizes[1], hash="opaque-cloud-hash")])
            report, text = run_fixture_main(DUP, tree)
            group = report["groups"][0]
            self.assertIs(group.get("exact"), False)
            self.assertEqual(group["match_type"], "name_candidate")
            self.assertNotIn("完全重复", text)
            self.assertEqual(group["evidence"]["same_size"], sizes[0] == sizes[1])
            self.assertEqual(group["evidence"]["remote_hash_match"], sizes[0] == sizes[1])

    def test_trash_command_ids_are_validated_atomically_and_deduplicated(self):
        for mod in (ADS, DUP):
            for bad in ("bad;printf injected", "$(printf injected)", "bad\nsecond", "--help", ""):
                out = io.StringIO()
                with self.subTest(module=mod.__name__, bad=bad), redirect_stdout(out):
                    with self.assertRaises(ValueError):
                        mod.emit_trash_cmd(["safe_id", bad])
                    self.assertEqual(out.getvalue(), "")
            out = io.StringIO()
            with redirect_stdout(out):
                mod.emit_trash_cmd(["safe_id", "safe_id", "safe-2"])
            self.assertEqual(shlex.split(out.getvalue()), ["pikpak", "rm", "safe_id", "safe-2"])


class CrossTypeSafety(unittest.TestCase):
    def test_documents_archives_subtitles_and_audio_never_join_video_versions(self):
        files = [item("v1", "Neutral.Movie.1080p.mkv"), item("v2", "Neutral.Movie.2160p.mp4")]
        files += [item(ext, "Neutral.Movie." + ext) for ext in ("pdf", "zip", "srt", "mp3")]
        report, _ = run_fixture_main(DUP, fixture(files))
        ids = {m["id"] for g in report["groups"] for m in [g["keeper"], *g["trash_candidates"]]}
        self.assertEqual(ids, {"v1", "v2"})

    def test_highest_resolution_compares_long_edge_not_just_width(self):
        portrait = DUP.member_record([], item("portrait", params={"width": "2160", "height": "3840"}))
        landscape = DUP.member_record([], item("landscape", params={"width": "2560", "height": "1440"}))
        self.assertEqual(DUP.pick_keeper([portrait, landscape], "highest-res")["id"], "portrait")


def invoke_main(mod, argv):
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with patch.object(sys, "argv", ["script", *argv]), patch.object(mod.subprocess, "run", side_effect=AssertionError("no real CLI")), redirect_stdout(out), redirect_stderr(err):
        try:
            mod.main()
        except SystemExit as stopped:
            code = stopped.code
    return code, out.getvalue(), err.getvalue()


class FrozenApproval(unittest.TestCase):
    def test_only_explicit_approved_ids_from_hash_bound_report_are_printed(self):
        import hashlib
        for mod in (ADS, DUP):
            tree = fixture([item("a", "Neutral.Movie.1080p.mkv" if mod is DUP else "neutral.url"),
                            item("b", "Neutral.Movie.2160p.mkv" if mod is DUP else "second.url")])
            report, _ = run_fixture_main(mod, tree)
            selected = "b" if mod is DUP else "a"
            with self.subTest(module=mod.__name__), tempfile.TemporaryDirectory() as tmp:
                source = Path(tmp) / "frozen.json"
                raw = json.dumps(report).encode()
                source.write_bytes(raw)
                args = ["--from-report", str(source), "--report-sha256", hashlib.sha256(raw).hexdigest(),
                        "--approve-id", selected, "--emit-trash-cmd"]
                if mod is ADS:
                    args += ["high"]
                code, out, err = invoke_main(mod, args)
                self.assertEqual(code, 0, err)
                self.assertEqual(shlex.split(out), ["pikpak", "rm", selected])
                source.write_bytes(raw + b"\n")
                code, out, _ = invoke_main(mod, args)
                self.assertEqual(code, 2)
                self.assertNotIn("pikpak rm ", out)
                source.write_bytes(raw)
                args[args.index(selected)] = "not-approved-candidate"
                code, out, _ = invoke_main(mod, args)
                self.assertEqual(code, 2)
                self.assertNotIn("pikpak rm ", out)

    def test_scanning_cannot_emit_unfrozen_commands(self):
        for mod in (ADS, DUP):
            with tempfile.TemporaryDirectory() as tmp:
                source = Path(tmp) / "fixture.json"
                source.write_text(json.dumps(fixture([item()])))
                args = ["--from-fixture", str(source), "--emit-trash-cmd"]
                if mod is ADS:
                    args += ["high"]
                code, out, _ = invoke_main(mod, args)
                self.assertEqual(code, 2)
                self.assertNotIn("pikpak rm ", out)


class ControlledInputs(unittest.TestCase):
    def test_optional_times_normalize_null_and_reject_bad_types_or_strings_without_stale_success(self):
        for mod in MODULES:
            for field in ("modified_time", "created_time"):
                for value in (None, 123, False, [], {}, "not-a-timestamp"):
                    for destination in ("-", "file"):
                        with self.subTest(module=mod.__name__, field=field, value=value, destination=destination), tempfile.TemporaryDirectory() as tmp:
                            source, target = Path(tmp) / "fixture.json", Path(tmp) / "report.json"
                            name = "Neutral.Movie.1080p.mkv" if mod is DUP else "neutral.url"
                            second = "Neutral.Movie.2160p.mkv" if mod is DUP else "second.url"
                            source.write_text(json.dumps(fixture([item("a", name, **{field: value}), item("b", second)])))
                            target.write_text('{"complete":true,"stale":true}')
                            code, out, err = invoke_main(mod, ["--from-fixture", str(source), "--json", "-" if destination == "-" else str(target)])
                            self.assertEqual(code, 0 if value is None else 2, err)
                            report = json.loads(out if destination == "-" else target.read_text())
                            self.assertNotIn("stale", report)
                            self.assertIs(report["complete"], value is None)
                            if value is not None:
                                self.assertEqual(report["error_code"], "INVALID_METADATA")
                                self.assertNotIn("扫描完成", out + err)
                                self.assertNotIn("pikpak rm ", out)
                            elif field == "modified_time":
                                members = report["high"] if mod is ADS else report["top_files"] if mod is STORAGE else [report["groups"][0]["keeper"], *report["groups"][0]["trash_candidates"]]
                                self.assertEqual(next(m for m in members if m["id"] == "a")["modified"], "")

    def test_fixture_config_and_numeric_argument_failures_replace_old_reports_and_keep_stdout_json(self):
        common = ("bad-json", "bad-utf8", "missing-fixture", "empty-fixture-path", "nul-fixture-path", "timeout-invalid",
                  "timeout-zero", "timeout-negative", "timeout-huge", "max-invalid", "max-zero", "max-negative")
        for mod in MODULES:
            scenarios = common + (("min-invalid", "min-negative") if mod is DUP else ("top-invalid", "top-negative", "depth-negative") if mod is STORAGE else ())
            for scenario in scenarios:
                for destination in ("-", "file"):
                    with self.subTest(module=mod.__name__, scenario=scenario, destination=destination), tempfile.TemporaryDirectory() as tmp:
                        source, target = Path(tmp) / "fixture.json", Path(tmp) / "report.json"
                        source.write_text(json.dumps(fixture([item()])))
                        target.write_text('{"complete":true,"stale":true}')
                        argv = ["script", "--json", "-" if destination == "-" else str(target)]
                        env = {}
                        if scenario.startswith("timeout-"):
                            env["PIKPAK_TIMEOUT"] = {"timeout-invalid": "neutral-config-sentinel", "timeout-zero": "0", "timeout-negative": "-1", "timeout-huge": "999999999999999999999"}[scenario]
                            expected = "INVALID_CONFIG"
                        else:
                            argv += ["--from-fixture", "" if scenario == "empty-fixture-path" else str(source)]
                            if scenario == "nul-fixture-path":
                                argv[-1] = "neutral\x00fixture"
                            expected = "INVALID_FIXTURE" if "fixture" in scenario or scenario.startswith("bad-") else "INVALID_ARGUMENTS"
                            if scenario == "bad-json":
                                source.write_text('{"neutral":')
                            elif scenario == "bad-utf8":
                                source.write_bytes(b"\xff")
                            elif scenario == "missing-fixture":
                                source.unlink()
                            elif scenario.startswith(("max-", "min-", "top-", "depth-")):
                                option, kind = scenario.split("-", 1)
                                argv += [{"max": "--max-items", "min": "--min-size", "top": "--top", "depth": "--depth"}[option],
                                         "neutral-argument-sentinel" if kind == "invalid" else "0" if kind == "zero" else "-1"]
                        out, err = io.StringIO(), io.StringIO()
                        code = 0
                        fake = SimpleNamespace(returncode=0, stdout='{"files":[]}', stderr="")
                        with patch.object(sys, "argv", argv), patch.dict(os.environ, env), \
                                patch.object(subprocess, "run", return_value=fake) as cli, \
                                redirect_stdout(out), redirect_stderr(err):
                            try:
                                mod.main()
                            except SystemExit as stopped:
                                code = stopped.code
                        cli.assert_not_called()
                        self.assertEqual(code, 2, err.getvalue())
                        report = json.loads(out.getvalue() if destination == "-" else target.read_text())
                        self.assertIs(report["complete"], False)
                        self.assertEqual(report["error_code"], expected)
                        self.assertNotIn("stale", report)
                        self.assertNotIn("sentinel", out.getvalue() + err.getvalue() + json.dumps(report))
                        self.assertNotIn("扫描完成", out.getvalue() + err.getvalue())

    def test_unwritable_report_has_safe_failure_json_not_an_exception_or_success_display(self):
        for mod in MODULES:
            with self.subTest(module=mod.__name__), tempfile.TemporaryDirectory() as tmp:
                source, target = Path(tmp) / "fixture.json", Path(tmp) / "report-directory"
                source.write_text(json.dumps(fixture([item()])))
                target.mkdir()
                code, out, err = invoke_main(mod, ["--from-fixture", str(source), "--json", str(target)])
                self.assertEqual(code, 2)
                report = json.loads(out)
                self.assertIs(report["complete"], False)
                self.assertEqual(report["error_code"], "REPORT_WRITE_FAILED")
                self.assertNotIn("扫描完成", out + err)

    def test_size_display_overflow_is_a_controlled_failure_not_a_stale_success(self):
        for mod in MODULES:
            for destination in ("-", "file"):
                with self.subTest(module=mod.__name__, destination=destination), tempfile.TemporaryDirectory() as tmp:
                    source, target = Path(tmp) / "fixture.json", Path(tmp) / "report.json"
                    name = "Neutral.Movie.1080p.mkv" if mod is DUP else "neutral.url"
                    source.write_text(json.dumps(fixture([item("a", name, "1" + "0" * 400)])))
                    target.write_text('{"complete":true,"stale":true}')
                    code, out, err = invoke_main(mod, ["--from-fixture", str(source), "--json", "-" if destination == "-" else str(target)])
                    self.assertEqual(code, 2)
                    report = json.loads(out if destination == "-" else target.read_text())
                    self.assertIs(report["complete"], False)
                    self.assertNotIn("stale", report)
                    self.assertNotIn("扫描完成", out + err)


class StructuredReports(unittest.TestCase):
    def test_json_stdout_is_pure_for_each_scanner(self):
        for mod in MODULES:
            with tempfile.TemporaryDirectory() as tmp:
                source = Path(tmp) / "fixture.json"
                source.write_text(json.dumps(fixture([item()])))
                code, out, err = invoke_main(mod, ["--from-fixture", str(source), "--json", "-"])
                self.assertEqual(code, 0, err)
                self.assertTrue(out.lstrip().startswith("{"), "stdout 必须只有 JSON")
                self.assertIs(json.loads(out)["complete"], True)

    def test_storage_json_contains_ranked_file_ids_paths_and_unknown_account(self):
        report, _ = run_fixture_main(STORAGE, fixture([item("small", size="10"), item("large", size="20")]), ["--top", "1"])
        self.assertIn("top_files", report)
        self.assertEqual([f["id"] for f in report["top_files"]], ["large"])
        self.assertEqual(report["top_files"][0]["path"], "neutral.url")
        self.assertIsNone(report["account_overview"])


class StrictByteEvidence(unittest.TestCase):
    def test_ad_bytes_remain_unknown_and_bad_strings_cannot_prove_a_short_video(self):
        invalid = (None, False, True, -1, "-200000", "200000 units", 200000.0, {}, [],
                   "1e6", "0x20", " 200000 ", "+200000")
        for size in invalid:
            with self.subTest(size=size):
                report, _ = run_fixture_main(ADS, fixture([item("a", "neutral.url", size), item("b", "宣传.mkv", size)]))
                self.assertIsNone(report["high"][0]["size"])
                self.assertIs(report["high"][0]["size_known"], False)
                self.assertEqual(report["high"][0]["size_h"], "未知")
                self.assertEqual(report["review"], [])
                self.assertEqual(report["unknown_size_files"], 2)
                self.assertIs(report["size_complete"], False)
        for size in (0, "0", 23, "23"):
            with self.subTest(known_size=size):
                report, _ = run_fixture_main(ADS, fixture([item("a", "neutral.url", size)]))
                self.assertEqual(report["high"][0]["size"], 0 if size in (0, "0") else 23)
                self.assertIs(report["high"][0]["size_known"], True)
                self.assertIs(report["size_complete"], True)

    def test_dedup_unknown_bytes_never_become_size_hash_or_keeper_evidence(self):
        invalid = (None, False, True, -1, "-200000", "200000 units", 200000.0, {}, [],
                   "1e6", "0x20", " 200000 ", "+200000")
        scenarios = [[size, size] for size in invalid] + [["-200000", "200000 units"], ["200000", "300000", None]]
        for policy in ("newest", "largest", "highest-res"):
            for sizes in scenarios:
                with self.subTest(policy=policy, sizes=sizes):
                    files = [item(chr(97 + i), f"Neutral.Movie.{res}p.mkv", size, hash="neutral-opaque-hash")
                             for i, (res, size) in enumerate(zip((1080, 2160, 720), sizes))]
                    report, _ = run_fixture_main(DUP, fixture(files), ["--policy", policy])
                    self.assertIs(report["complete"], True)
                    self.assertEqual(report["groups"], [])
                    self.assertIs(report["size_complete"], False)
                    self.assertIs(report["analysis_complete"], False)
                    self.assertEqual(report["unknown_size_files"], 2 if len(sizes) == 2 else 1)
                    for member in report["unassessed_files"]:
                        self.assertIsNone(member["size"])
                        self.assertIs(member["size_known"], False)
                        self.assertEqual(member["size_h"], "未知")
                    pending = report["unassessed_groups"][0]
                    self.assertIsNone(pending["keeper"])
                    self.assertEqual(pending["trash_candidates"], [])
                    self.assertIs(pending["evidence"]["same_size"], False)
                    self.assertIs(pending["evidence"]["remote_hash_match"], False)
                    self.assertIsNone(DUP.pick_keeper(pending["members"], policy))
        report, _ = run_fixture_main(DUP, fixture([item("a", "Neutral.Movie.1080p.mkv", 0),
                                                 item("b", "Neutral.Movie.2160p.mkv", "0")]), ["--min-size", "0"])
        self.assertIs(report["size_complete"], True)
        self.assertIs(report["groups"][0]["evidence"]["same_size"], True)
        self.assertEqual(report["groups"][0]["keeper"]["size"], 0)


class StrictResolutionEvidence(unittest.TestCase):
    def test_invalid_pixels_never_become_highest_res_keeper_or_trash_evidence(self):
        invalid = (None, False, True, -1, 0, "-99999", "0", "99999 pixels", 99999.0,
                   "99999.0", {}, [], {"pixels": 99999}, [99999], "1e5", "0x1869f",
                   " 99999 ", "+99999", "９９９９９", "99999\n", "9" * 5000,
                   10 ** 100, 2147483648, "2147483648")
        cases = [{"width": "-99999", "height": "-1"}]
        cases += [{"width": "3840", "height": "2160", field: value}
                  for field in ("width", "height") for value in invalid]
        for index, params in enumerate(cases):
            for reverse in (False, True):
                with self.subTest(case=index, reverse=reverse):
                    bad = item("bad", "Neutral.Movie.1080p.mkv", params=params)
                    valid = item("valid", "Neutral.Movie.2160p.mkv",
                                 params={"width": "3840", "height": "2160"})
                    pair = [valid, bad] if reverse else [bad, valid]
                    report, text = run_fixture_main(DUP, fixture(pair), ["--policy", "highest-res"])
                    self.assertEqual(report["groups"], [])
                    self.assertIs(report["complete"], True)
                    self.assertIs(report["size_complete"], True)
                    self.assertIs(report["analysis_complete"], False)
                    self.assertEqual(len(report["unassessed_groups"]), 1)
                    pending = report["unassessed_groups"][0]
                    self.assertEqual(pending["reason"], "unknown_resolution")
                    self.assertIsNone(pending["keeper"])
                    self.assertEqual(pending["trash_candidates"], [])
                    members = {m["id"]: m for m in pending["members"]}
                    self.assertEqual(set(members), {"bad", "valid"})
                    self.assertEqual(members["bad"]["resolution"], "")
                    self.assertEqual(members["valid"]["resolution"], "3840x2160")
                    self.assertIsNone(DUP.pick_keeper(pending["members"], "highest-res"))
                    self.assertIsNone(DUP.resolution_of(bad))
                    self.assertIn("分辨率", text)
                    self.assertNotIn("建议清理", text)
                    self.assertNotIn("pikpak rm ", text)

    def test_missing_or_partial_dimensions_do_not_rank_against_known_or_unknown_members(self):
        for params in (None, {}, {"width": "3840"}, {"height": "2160"}):
            for other in (None, {"width": "3840", "height": "2160"}):
                with self.subTest(params=params, other=other), tempfile.TemporaryDirectory() as tmp:
                    source = Path(tmp) / "fixture.json"
                    source.write_text(json.dumps(fixture([
                        item("a", "Neutral.Movie.1080p.mkv", params=params),
                        item("b", "Neutral.Movie.2160p.mkv", params=other),
                    ])))
                    code, out, err = invoke_main(DUP, ["--from-fixture", str(source), "--policy", "highest-res", "--json", "-"])
                    self.assertEqual(code, 0, err)
                    report = json.loads(out)
                    self.assertIs(report["complete"], True)
                    self.assertIs(report["analysis_complete"], False)
                    self.assertEqual(report["groups"], [])
                    pending = report["unassessed_groups"][0]
                    self.assertEqual(pending["reason"], "unknown_resolution")
                    self.assertIsNone(pending["keeper"])
                    self.assertEqual(pending["trash_candidates"], [])
                    self.assertEqual(pending["members"][0]["resolution"], "")

    def test_valid_integer_dimensions_preserve_long_edge_size_and_time_ranking_end_to_end(self):
        cases = (
            ({"width": 2160, "height": 3840}, {"width": "2560", "height": "1440"}, 200000, "", "3840"),
            ({"width": "0002160", "height": "0003840"}, {"width": 2560, "height": 1440}, 200000, "", "3840"),
            ({"width": "2160", "height": "3840"}, {"width": 3840, "height": 2160}, 300000, "", "3840"),
            ({"width": "2160", "height": "3840"}, {"width": 3840, "height": 2160}, 200000, "2026-01-01T00:00:00Z", "3840"),
            ({"width": 1, "height": 2147483647}, {"width": 3840, "height": 2160}, 200000, "", "2147483647"),
        )
        for winner, loser, size, modified, long_edge in cases:
            with self.subTest(winner=winner, size=size, modified=modified):
                report, _ = run_fixture_main(DUP, fixture([
                    item("loser", "Neutral.Movie.1080p.mkv", params=loser),
                    item("winner", "Neutral.Movie.2160p.mkv", size, params=winner, modified_time=modified),
                ]), ["--policy", "highest-res"])
                self.assertIs(report["analysis_complete"], True)
                self.assertEqual(report["unassessed_groups"], [])
                self.assertEqual(report["groups"][0]["keeper"]["id"], "winner")
                self.assertEqual([m["id"] for m in report["groups"][0]["trash_candidates"]], ["loser"])
                self.assertEqual(DUP.resolution_of({"params": winner}), int(long_edge))

    def test_non_resolution_policies_fail_closed_on_invalid_pixels(self):
        for policy in ("newest", "largest"):
            with self.subTest(policy=policy):
                report, _ = run_fixture_main(DUP, fixture([
                    item("a", "Neutral.Movie.1080p.mkv", params={"width": "-99999", "height": "-1"}),
                    item("b", "Neutral.Movie.2160p.mkv", "300000",
                         params={"width": "3840", "height": "2160"},
                         modified_time="2026-01-01T00:00:00Z"),
                ]), ["--policy", policy])
                self.assertIs(report["analysis_complete"], False)
                self.assertEqual(report["groups"], [])
                pending = report["unassessed_groups"][0]
                self.assertEqual(pending["reason"], "unknown_resolution")
                self.assertIsNone(pending["keeper"])
                self.assertEqual(pending["trash_candidates"], [])

    def test_invalid_dimensions_fail_closed_for_every_policy_without_echoing_nested_values(self):
        invalid = (
            -1, True, 99999.0, 10 ** 100,
            {"nested": {"payload": "neutral-resolution-sentinel"}},
            [99999], "99999 pixels", "1e5",
        )
        for policy in ("newest", "largest", "highest-res"):
            for field in ("width", "height"):
                for value in invalid:
                    with self.subTest(policy=policy, field=field, value=value):
                        params: dict[str, object] = {"width": "3840", "height": "2160"}
                        params[field] = value
                        report, text = run_fixture_main(DUP, fixture([
                            item("bad", "Neutral.Movie.1080p.mkv", params=params),
                            item("valid", "Neutral.Movie.2160p.mkv",
                                 params={"width": "3840", "height": "2160"}),
                        ]), ["--policy", policy])
                        self.assertIs(report["complete"], True)
                        self.assertIs(report["size_complete"], True)
                        self.assertIs(report["analysis_complete"], False)
                        self.assertEqual(report["groups"], [])
                        self.assertEqual(len(report["unassessed_groups"]), 1)
                        pending = report["unassessed_groups"][0]
                        self.assertEqual(pending["reason"], "unknown_resolution")
                        self.assertIsNone(pending["keeper"])
                        self.assertEqual(pending["trash_candidates"], [])
                        members = {member["id"]: member for member in pending["members"]}
                        self.assertEqual(members["bad"]["resolution"], "")
                        self.assertEqual(members["valid"]["resolution"], "3840x2160")
                        self.assertNotIn("neutral-resolution-sentinel", json.dumps(report, ensure_ascii=False))
                        self.assertNotIn("neutral-resolution-sentinel", text)
                        self.assertNotIn("✓ 保留", text)
                        self.assertNotIn("✗ 清理", text)
                        self.assertNotIn("pikpak rm ", text)

    def test_highest_res_unassessed_ids_are_not_frozen_approval_candidates(self):
        report, _ = run_fixture_main(DUP, fixture([
            item("bad", "Neutral.Movie.1080p.mkv", params={"width": "-99999", "height": "-1"}),
            item("unknown_peer", "Neutral.Movie.2160p.mkv", params={"width": "3840", "height": "2160"}),
            item("safe_trash", "Neutral.Other.1080p.mkv", params={"width": 1920, "height": 1080}),
            item("safe_keeper", "Neutral.Other.2160p.mkv", params={"width": 3840, "height": 2160}),
        ]), ["--policy", "highest-res"])
        self.assertEqual(len(report["groups"]), 1)
        self.assertEqual(len(report["unassessed_groups"]), 1)
        self.assertIs(report["analysis_complete"], False)
        with tempfile.TemporaryDirectory() as tmp:
            frozen = Path(tmp) / "report.json"
            raw = json.dumps(report).encode()
            frozen.write_bytes(raw)
            base = ["--from-report", str(frozen), "--report-sha256", hashlib.sha256(raw).hexdigest(), "--emit-trash-cmd"]
            code, out, err = invoke_main(DUP, [*base, "--approve-id", "safe_trash"])
            self.assertEqual(code, 0, err)
            self.assertEqual(shlex.split(out), ["pikpak", "rm", "safe_trash"])
            for denied in ("bad", "unknown_peer", "safe_keeper"):
                for mixed in (False, True):
                    with self.subTest(denied=denied, mixed=mixed):
                        args = [*base, "--approve-id", denied]
                        if mixed:
                            args += ["--approve-id", "safe_trash"]
                        code, out, _ = invoke_main(DUP, args)
                        self.assertEqual(code, 2)
                        self.assertEqual(out, "")


class MetadataIntegrity(unittest.TestCase):
    def test_storage_unknown_sizes_are_not_reported_as_known_zero_bytes(self):
        unknown = item("unknown", size=None)
        report, _ = run_fixture_main(STORAGE, fixture([unknown, item("known", size="23")]))
        self.assertEqual(report.get("unknown_size_files"), 1)
        self.assertIs(report["total_size_is_lower_bound"], True)
        self.assertEqual(report["total_size"], 23)
        by_id = {f["id"]: f for f in report["top_files"]}
        self.assertIsNone(by_id["unknown"]["size"])

    def test_fixed_page_and_depth_budgets_stop_incomplete(self):
        pages = fixture([], "p2", {"p2": {"files": [], "next_page_token": ""}})
        nested = fixture([folder("A")])
        nested["A"] = {"": {"files": [], "next_page_token": ""}}
        for mod in MODULES:
            for cap, tree in (("MAX_PAGES", pages), ("MAX_DEPTH", nested)):
                with self.subTest(module=mod.__name__, cap=cap), patch.object(mod, cap, 1):
                    with self.assertRaises(RuntimeError) as stopped:
                        scan(mod, tree)
                    self.assertTrue(getattr(stopped.exception, "truncated", False))

    def test_bad_response_shapes_fail_closed(self):
        bad_pages = [[], {}, {"files": None}, {"files": [None]},
                     {"files": [], "next_page_token": 0},
                     {"files": [], "next_page_token": False},
                     {"files": [item(params=[])]}, {"files": [item(hash=123)]}]
        for mod in MODULES:
            for page in bad_pages:
                with self.subTest(module=mod.__name__, page=page):
                    with self.assertRaises(RuntimeError):
                        mod.validate_page(page)

    def test_duplicate_folder_id_is_scanned_once(self):
        tree = fixture([folder("A"), folder("A")])
        tree["A"] = {"": {"files": [item()], "next_page_token": ""}}
        for mod in MODULES:
            result, counts = scan(mod, tree)
            self.assertEqual(counts["folders"], 1)
            self.assertEqual(counts["files"], 1)


class BoundaryVerification(unittest.TestCase):
    def test_default_guard_blocks_process_network_and_notification_entry_points(self):
        self.assertTrue(Path.home().resolve().is_relative_to(Path(os.environ["TMPDIR"]).resolve().parent))
        self.assertTrue(Path(os.environ["PIKPAK_BIN"]).is_relative_to(Path(os.environ["HOME"]).parent))
        attempts = (
            lambda: subprocess.run(["pikpak", "ls"]),
            lambda: subprocess.Popen(["osascript", "-e", "neutral"]),
            lambda: subprocess.call(["notify-send", "neutral"]),
            lambda: socket.create_connection(("example.test", 443)),
            lambda: socket.getaddrinfo("example.test", 443),
            lambda: urllib.request.urlopen("https://example.test/neutral"),
            lambda: os.system("neutral-command-denied"),
        )
        for attempt in attempts:
            with self.assertRaises(AssertionError):
                attempt()
        with socket.socket() as sock:
            with self.assertRaises(AssertionError):
                sock.connect(("127.0.0.1", 1))

    def test_same_page_file_folder_collision_is_incomplete_in_both_orders(self):
        for mod in MODULES:
            for reverse in (False, True):
                pair = [item("same"), folder("same")]
                tree = fixture(list(reversed(pair)) if reverse else pair)
                tree["same"] = {"": {"files": [item("child")]}}
                with self.subTest(module=mod.__name__, reverse=reverse), tempfile.TemporaryDirectory() as tmp:
                    source = Path(tmp) / "fixture.json"
                    source.write_text(json.dumps(tree))
                    code, out, _ = invoke_main(mod, ["--from-fixture", str(source), "--json", "-"])
                    self.assertEqual(code, 2)
                    self.assertIs(json.loads(out)["identity_conflict"], True)

    def test_protected_subtitles_password_text_and_archives_stay_out_of_ad_candidates(self):
        report, _ = run_fixture_main(ADS, fixture([item("sub", "推广.srt"), item("pwd", "广告password.txt"), item("archive", "广告.zip"), item("ad", "neutral.url")]))
        self.assertEqual({m["id"] for m in report["protected"]}, {"sub", "pwd"})
        self.assertEqual({m["id"] for m in report["high"]}, {"ad"})
        self.assertEqual(report["review"], [])
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "frozen.json"
            raw = json.dumps(report).encode()
            source.write_bytes(raw)
            for protected in ("sub", "pwd", "archive"):
                code, out, _ = invoke_main(ADS, ["--from-report", str(source), "--report-sha256", hashlib.sha256(raw).hexdigest(), "--approve-id", protected, "--emit-trash-cmd", "high"])
                self.assertEqual(code, 2)
                self.assertEqual(out, "")

    def test_storage_display_depth_does_not_limit_scan_or_total(self):
        tree = fixture([folder("A")])
        tree["A"] = {"": {"files": [folder("B")]}}
        tree["B"] = {"": {"files": [item("deep", "neutral.data", "51")]}}
        for depth in (1, 3):
            report, _ = run_fixture_main(STORAGE, tree, ["--depth", str(depth), "--top", "0"])
            self.assertEqual(report["total_size"], 51)
            self.assertEqual(report["total_files"], 1)
            self.assertEqual(report["top_files"], [])
            self.assertEqual(report["tree"]["children"][0]["children"][0]["file_count"], 1)

    def test_cli_invalid_json_timeout_and_os_errors_are_safely_classified(self):
        raw = "neutral-diagnostic-sentinel https://example.test/?token=neutral-query-sentinel"
        cases = (
            SimpleNamespace(returncode=0, stdout=raw, stderr=""),
            subprocess.TimeoutExpired("neutral", 90, output=raw, stderr=raw),
            FileNotFoundError(raw), PermissionError(raw),
            UnicodeDecodeError("utf-8", b"\xff", 0, 1, raw),
        )
        for mod in MODULES:
            for case in cases:
                for destination in ("-", "file"):
                    with self.subTest(module=mod.__name__, case=type(case).__name__, destination=destination), tempfile.TemporaryDirectory() as tmp:
                        target = Path(tmp) / "report.json"
                        target.write_text('{"complete":true,"stale":true}')
                        out, err = io.StringIO(), io.StringIO()
                        with patch.object(subprocess, "run") as cli, patch.object(sys, "argv", ["script", "--json", "-" if destination == "-" else str(target)]), redirect_stdout(out), redirect_stderr(err):
                            if isinstance(case, Exception):
                                cli.side_effect = case
                            else:
                                cli.return_value = case
                            with self.assertRaises(SystemExit) as stopped:
                                mod.main()
                        self.assertEqual(stopped.exception.code, 2)
                        report = json.loads(out.getvalue() if destination == "-" else target.read_text())
                        self.assertIs(report["complete"], False)
                        self.assertNotIn("stale", report)
                        self.assertNotIn("sentinel", out.getvalue() + err.getvalue() + json.dumps(report))


class ReportWriteSafety(unittest.TestCase):
    @staticmethod
    def _broken_fixture(tmp):
        source = Path(tmp) / "fixture.json"
        source.write_text(json.dumps({"": {}}))
        return source

    @staticmethod
    def _assert_safe_failure(testcase, code, out, err):
        testcase.assertEqual(code, 2, err)
        testcase.assertTrue(out.strip(), "报告写入失败必须把安全失败 JSON 输出到 stdout")
        report = json.loads(out)
        testcase.assertIs(report["complete"], False)
        testcase.assertEqual(report["error_code"], "REPORT_WRITE_FAILED")
        testcase.assertTrue(report["errors"])
        testcase.assertNotIn("stale", out + err)
        testcase.assertNotIn("扫描完成", out + err)

    def test_error_report_rejects_symlink_without_updating_backing_file(self):
        for mod in MODULES:
            with self.subTest(module=mod.__name__), tempfile.TemporaryDirectory() as tmp:
                source = self._broken_fixture(tmp)
                backing = Path(tmp) / "backing.json"
                target = Path(tmp) / "report.json"
                stale = b'{"complete":true,"stale":true}'
                backing.write_bytes(stale)
                target.symlink_to(backing)
                code, out, err = invoke_main(mod, ["--from-fixture", str(source), "--json", str(target)])
                self._assert_safe_failure(self, code, out, err)
                self.assertEqual(backing.read_bytes(), stale)
                self.assertTrue(target.is_symlink())

    def test_error_report_rejects_non_regular_target(self):
        for mod in MODULES:
            with self.subTest(module=mod.__name__), tempfile.TemporaryDirectory() as tmp:
                source = self._broken_fixture(tmp)
                code, out, err = invoke_main(mod, ["--from-fixture", str(source), "--json", "/dev/null"])
                self._assert_safe_failure(self, code, out, err)

    def test_error_report_rejects_hardlink_before_truncation(self):
        for mod in MODULES:
            with self.subTest(module=mod.__name__), tempfile.TemporaryDirectory() as tmp:
                source = self._broken_fixture(tmp)
                target = Path(tmp) / "report.json"
                alias = Path(tmp) / "alias.json"
                stale = b'{"complete":true,"stale":true}'
                target.write_bytes(stale)
                os.link(target, alias)
                code, out, err = invoke_main(mod, ["--from-fixture", str(source), "--json", str(target)])
                self._assert_safe_failure(self, code, out, err)
                self.assertEqual(target.read_bytes(), stale)
                self.assertEqual(alias.read_bytes(), stale)

    def test_error_report_forces_exact_0600_for_new_and_existing_regular_files(self):
        for mod in MODULES:
            for existing in (False, True):
                with self.subTest(module=mod.__name__, existing=existing), tempfile.TemporaryDirectory() as tmp:
                    source = self._broken_fixture(tmp)
                    target = Path(tmp) / "report.json"
                    if existing:
                        target.write_text('{"complete":true,"stale":true}')
                        target.chmod(0o644)
                    old_umask = os.umask(0o022)
                    try:
                        code, out, err = invoke_main(mod, ["--from-fixture", str(source), "--json", str(target)])
                    finally:
                        os.umask(old_umask)
                    self.assertEqual(code, 2, err)
                    self.assertEqual(out, "")
                    report = json.loads(target.read_text())
                    self.assertIs(report["complete"], False)
                    self.assertEqual(os.stat(target).st_mode & 0o777, 0o600)
    def test_success_report_rejects_symlink_without_updating_backing_file(self):
        for mod in MODULES:
            with self.subTest(module=mod.__name__), tempfile.TemporaryDirectory() as tmp:
                source = Path(tmp) / "fixture.json"
                source.write_text(json.dumps(fixture([item("success", "neutral.mkv")])))
                backing = Path(tmp) / "backing.json"
                target = Path(tmp) / "report.json"
                stale = b'{"complete":true,"stale":true}'
                backing.write_bytes(stale)
                target.symlink_to(backing)
                code, out, err = invoke_main(mod, ["--from-fixture", str(source), "--json", str(target)])
                self._assert_safe_failure(self, code, out, err)
                self.assertEqual(backing.read_bytes(), stale)
                self.assertTrue(target.is_symlink())

    def test_success_report_rejects_non_regular_target(self):
        for mod in MODULES:
            with self.subTest(module=mod.__name__), tempfile.TemporaryDirectory() as tmp:
                source = Path(tmp) / "fixture.json"
                source.write_text(json.dumps(fixture([item("success", "neutral.mkv")])))
                code, out, err = invoke_main(mod, ["--from-fixture", str(source), "--json", "/dev/null"])
                self._assert_safe_failure(self, code, out, err)

    def test_success_report_rejects_hardlink_before_truncation(self):
        for mod in MODULES:
            with self.subTest(module=mod.__name__), tempfile.TemporaryDirectory() as tmp:
                source = Path(tmp) / "fixture.json"
                source.write_text(json.dumps(fixture([item("success", "neutral.mkv")])))
                target = Path(tmp) / "report.json"
                alias = Path(tmp) / "alias.json"
                stale = b'{"complete":true,"stale":true}'
                target.write_bytes(stale)
                os.link(target, alias)
                code, out, err = invoke_main(mod, ["--from-fixture", str(source), "--json", str(target)])
                self._assert_safe_failure(self, code, out, err)
                self.assertEqual(target.read_bytes(), stale)
                self.assertEqual(alias.read_bytes(), stale)

    def test_success_report_forces_exact_0600_for_new_and_existing_regular_files(self):
        for mod in MODULES:
            for existing in (False, True):
                with self.subTest(module=mod.__name__, existing=existing), tempfile.TemporaryDirectory() as tmp:
                    source = Path(tmp) / "fixture.json"
                    source.write_text(json.dumps(fixture([item("success", "neutral.mkv")])))
                    target = Path(tmp) / "report.json"
                    if existing:
                        target.write_text('{"complete":true,"stale":true}')
                        target.chmod(0o644)
                    old_umask = os.umask(0o022)
                    try:
                        code, out, err = invoke_main(mod, ["--from-fixture", str(source), "--json", str(target)])
                    finally:
                        os.umask(old_umask)
                    self.assertEqual(code, 0, err)
                    self.assertTrue(target.exists())
                    self.assertEqual(os.stat(target).st_mode & 0o777, 0o600)
                    report = json.loads(target.read_text())
                    self.assertIs(report["complete"], True)
                    self.assertNotIn("stale", report)


class JsonInputSafety(unittest.TestCase):
    @staticmethod
    def approval_args(mod, source, raw):
        args = ["--from-report", str(source), "--report-sha256", hashlib.sha256(raw).hexdigest(),
                "--approve-id", "neutral", "--emit-trash-cmd"]
        return args + (["high"] if mod is ADS else [])

    def test_deep_fixture_and_cli_json_fail_closed_without_stale_success(self):
        for mod in MODULES:
            for source_kind in ("fixture", "live"):
                for depth in (64, 1500):  # Root object plus arrays exceeds 64 containers.
                    for destination in ("-", "file"):
                        with self.subTest(module=mod.__name__, source=source_kind, depth=depth, destination=destination), tempfile.TemporaryDirectory() as tmp:
                            source, target = Path(tmp) / "fixture.json", Path(tmp) / "report.json"
                            raw = '{"files":[],"neutral":' + '[' * depth + '0' + ']' * depth + '}'
                            source.write_text(raw)
                            target.write_text('{"complete":true,"stale":true}')
                            args = ["--json", "-" if destination == "-" else str(target)]
                            if source_kind == "fixture":
                                args += ["--from-fixture", str(source)]
                            out, err = io.StringIO(), io.StringIO()
                            with patch.object(sys, "argv", ["script", *args]), patch.object(subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=raw, stderr="")) as cli, redirect_stdout(out), redirect_stderr(err):
                                with self.assertRaises(SystemExit) as stopped:
                                    mod.main()
                            self.assertEqual(stopped.exception.code, 2)
                            self.assertEqual(cli.call_count, int(source_kind == "live"))
                            report = json.loads(out.getvalue() if destination == "-" else target.read_text())
                            self.assertIs(report["complete"], False)
                            self.assertEqual(report["error_code"], "INVALID_FIXTURE" if source_kind == "fixture" else "INVALID_CLI_JSON")
                            self.assertNotIn("stale", report)
                            self.assertNotIn("pikpak rm ", out.getvalue())

    def test_deep_frozen_reports_have_controlled_error_and_no_approval_output(self):
        for mod in (ADS, DUP):
            for depth in (64, 1500):
                with self.subTest(module=mod.__name__, depth=depth), tempfile.TemporaryDirectory() as tmp:
                    source = Path(tmp) / "frozen.json"
                    raw = ('{"complete":true,"truncated":false,"neutral":' + '[' * depth + '0' + ']' * depth + '}').encode()
                    source.write_bytes(raw)
                    code, out, err = invoke_main(mod, self.approval_args(mod, source, raw))
                    self.assertEqual(code, 2)
                    self.assertEqual(out, "")
                    self.assertIn("INVALID_REPORT", err)

    def test_exact_byte_limit_accepts_regular_inputs_and_rejects_one_extra_byte(self):
        for mod in MODULES:
            for source_kind in ("fixture", "live", "report"):
                if source_kind == "report" and mod is STORAGE:
                    continue
                for extra in (0, 1):
                    with self.subTest(module=mod.__name__, source=source_kind, extra=extra), tempfile.TemporaryDirectory() as tmp:
                        source, target = Path(tmp) / "input.json", Path(tmp) / "report.json"
                        payload = fixture([]) if source_kind == "fixture" else {"files": []}
                        if source_kind == "report":
                            payload = {"complete": True, "truncated": False, "high": [{"id": "neutral"}], "protected": []} if mod is ADS else {"complete": True, "truncated": False, "groups": [{"keeper": {"id": "keeper"}, "trash_candidates": [{"id": "neutral"}]}]}
                        raw = json.dumps({**payload, "text": "中性\\\"[]{}\\\\"}, ensure_ascii=False).encode()
                        raw += b" " * (512 + extra - len(raw))
                        source.write_bytes(raw)
                        target.write_text('{"complete":true,"stale":true}')
                        args = self.approval_args(mod, source, raw) if source_kind == "report" else ["--json", str(target)]
                        if source_kind == "fixture":
                            args += ["--from-fixture", str(source)]
                        out, err = io.StringIO(), io.StringIO()
                        code = 0
                        with patch.object(mod, "MAX_JSON_BYTES", 512, create=True), patch.object(sys, "argv", ["script", *args]), patch.object(subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=raw.decode(), stderr="")) as cli, redirect_stdout(out), redirect_stderr(err):
                            try:
                                mod.main()
                            except SystemExit as stopped:
                                code = stopped.code
                        self.assertEqual(code, 2 if extra else 0, err.getvalue())
                        self.assertEqual(cli.call_count, int(source_kind == "live"))
                        if source_kind == "report":
                            self.assertEqual(out.getvalue(), "" if extra else "pikpak rm neutral\n")
                        else:
                            report = json.loads(target.read_text())
                            self.assertIs(report["complete"], not extra)
                            self.assertNotIn("stale", report)

    def test_depth_64_and_string_brackets_do_not_reject_normal_scoped_fixture(self):
        for mod in MODULES:
            with self.subTest(module=mod.__name__), tempfile.TemporaryDirectory() as tmp:
                source = Path(tmp) / "fixture.json"
                tree = {"": {"": {"error": "root must not be read"}}, "scoped": {"": {"files": [item("a")]}}}
                raw = json.dumps(tree)[:-1] + ',"quoted":' + json.dumps('\\"[]{}\\' * 2000) + ',"neutral":' + '[' * 63 + '0' + ']' * 63 + '}'
                source.write_text(raw)
                code, out, err = invoke_main(mod, ["--from-fixture", str(source), "--folder", "scoped", "--json", "-"])
                self.assertEqual(code, 0, err)
                self.assertIs(json.loads(out)["complete"], True)


class InputFileSafety(unittest.TestCase):
    def test_fifo_is_opened_nonblocking_and_rejected_before_reading(self):
        import time
        real_open, real_read = os.open, os.read
        for mod in MODULES:
            for source_kind in ("fixture", "report"):
                if source_kind == "report" and mod is STORAGE:
                    continue
                with self.subTest(module=mod.__name__, source=source_kind), tempfile.TemporaryDirectory() as tmp:
                    source, target = Path(tmp) / "fifo", Path(tmp) / "report.json"
                    os.mkfifo(source, 0o600)
                    target.write_text('{"complete":true,"stale":true}')
                    args = JsonInputSafety.approval_args(mod, source, b"") if source_kind == "report" else ["--from-fixture", str(source), "--json", str(target)]
                    opened = []
                    def checked_open(path, flags, *rest, **kw):
                        if str(path) == str(source):
                            self.assertTrue(flags & os.O_NONBLOCK, "FIFO must not block during open")
                            self.assertTrue(flags & os.O_NOFOLLOW, "input symlinks must not be followed")
                            opened.append(flags)
                        return real_open(path, flags, *rest, **kw)
                    def checked_read(fd, size):
                        import stat
                        self.assertTrue(stat.S_ISREG(os.fstat(fd).st_mode), "reject special files before read")
                        return real_read(fd, size)
                    start = time.monotonic()
                    with patch("builtins.open", side_effect=AssertionError("blocking open forbidden")), patch.object(os, "open", side_effect=checked_open), patch.object(os, "read", side_effect=checked_read):
                        code, out, err = invoke_main(mod, args)
                    self.assertLess(time.monotonic() - start, 0.5)
                    self.assertTrue(opened)
                    self.assertEqual(code, 2)
                    if source_kind == "report":
                        self.assertEqual(out, "")
                        self.assertIn("INVALID_REPORT", err)
                    else:
                        report = json.loads(target.read_text())
                        self.assertIs(report["complete"], False)
                        self.assertEqual(report["error_code"], "INVALID_FIXTURE")
                        self.assertNotIn("stale", report)

    def test_symlinks_hardlinks_directories_and_writable_inputs_are_rejected(self):
        for mod in MODULES:
            for source_kind in ("fixture", "report"):
                if source_kind == "report" and mod is STORAGE:
                    continue
                for kind in ("symlink", "hardlink", "directory", "group-write", "world-write"):
                    with self.subTest(module=mod.__name__, source=source_kind, kind=kind), tempfile.TemporaryDirectory() as tmp:
                        source, backing, target = Path(tmp) / "input", Path(tmp) / "backing", Path(tmp) / "report.json"
                        payload = fixture([]) if source_kind == "fixture" else {"complete": True, "truncated": False, "high": [{"id": "neutral"}], "protected": []} if mod is ADS else {"complete": True, "truncated": False, "groups": [{"keeper": {"id": "keeper"}, "trash_candidates": [{"id": "neutral"}]}]}
                        raw = json.dumps(payload).encode()
                        backing.write_bytes(raw)
                        if kind == "directory":
                            source.mkdir()
                        elif kind == "symlink":
                            source.symlink_to(backing)
                        elif kind == "hardlink":
                            os.link(backing, source)
                        else:
                            source.write_bytes(raw)
                            source.chmod(0o620 if kind == "group-write" else 0o602)
                        target.write_text('{"complete":true,"stale":true}')
                        args = JsonInputSafety.approval_args(mod, source, raw) if source_kind == "report" else ["--from-fixture", str(source), "--json", str(target)]
                        code, out, err = invoke_main(mod, args)
                        self.assertEqual(code, 2)
                        self.assertEqual(backing.read_bytes(), raw)
                        if source_kind == "report":
                            self.assertEqual(out, "")
                        else:
                            self.assertIs(json.loads(target.read_text())["complete"], False)

    def test_os_permission_errors_are_controlled_for_fixture_and_approval(self):
        for mod in MODULES:
            for source_kind in ("fixture", "report"):
                if source_kind == "report" and mod is STORAGE:
                    continue
                with self.subTest(module=mod.__name__, source=source_kind), tempfile.TemporaryDirectory() as tmp:
                    source = Path(tmp) / "input.json"
                    source.write_bytes(b"{}")
                    args = JsonInputSafety.approval_args(mod, source, b"{}") if source_kind == "report" else ["--from-fixture", str(source), "--json", "-"]
                    with patch.object(os, "open", side_effect=PermissionError("neutral-permission-sentinel")):
                        code, out, err = invoke_main(mod, args)
                    self.assertEqual(code, 2)
                    self.assertNotIn("sentinel", out + err)
                    if source_kind == "report":
                        self.assertEqual(out, "")
                    else:
                        self.assertIs(json.loads(out)["complete"], False)

    def test_regular_neutral_0644_and_private_0600_inputs_remain_usable_with_bounded_reads(self):
        real_read = os.read
        for mod in MODULES:
            for mode in (0o600, 0o644):
                with self.subTest(module=mod.__name__, mode=mode), tempfile.TemporaryDirectory() as tmp:
                    source = Path(tmp) / "fixture.json"
                    raw = json.dumps(fixture([item()])).encode()
                    source.write_bytes(raw)
                    source.chmod(mode)
                    reads = []
                    def short_read(fd, size):
                        self.assertGreater(size, 0)
                        self.assertLessEqual(size, mod.MAX_JSON_BYTES + 1)
                        reads.append(size)
                        return real_read(fd, min(size, 7))
                    with patch.object(os, "read", side_effect=short_read):
                        code, out, err = invoke_main(mod, ["--from-fixture", str(source), "--json", "-"])
                    self.assertEqual(code, 0, err)
                    self.assertTrue(reads)
                    self.assertIs(json.loads(out)["complete"], True)
                    self.assertEqual(source.stat().st_mode & 0o777, mode)


class CleanupDocumentation(unittest.TestCase):
    def test_report_permissions_and_input_limits_match_current_contract_without_maturity_upgrade(self):
        for skill in ("pikpak-ad-cleaner", "pikpak-dedup", "pikpak-storage-report"):
            for name in ("SKILL.md", "references/upstream-design.md"):
                with self.subTest(skill=skill, file=name):
                    text = (ROOT / "skills" / skill / name).read_text()
                    for token in ("0600", "O_NONBLOCK", "O_NOFOLLOW", "16 MiB", "64", "0644", "硬链接", "原子", "期限", "检查点"):
                        self.assertIn(token, text)
                    self.assertNotIn("未做报告原子写/0600", text)
                    self.assertNotIn("原子写/0600 报告权限", text)
                    self.assertNotIn("报告原子写与私密文件权限", text)
                    self.assertNotIn("原子写和私密权限", text)
                    self.assertNotIn("原子写、最小文件权限", text)


class PageEnvelopeSafety(unittest.TestCase):
    def test_validate_page_rejects_explicit_failure_envelopes(self):
        envelopes = (
            {"ok": False},
            {"success": False},
            {"error_code": "AUTH_FAILED"},
            {"code": "AUTH_FAILED"},
            {"error": "access denied"},
        )
        for mod in MODULES:
            for envelope in envelopes:
                page = {"files": [], "next_page_token": "", **envelope}
                with self.subTest(module=mod.__name__, envelope=envelope):
                    with self.assertRaises(RuntimeError):
                        mod.validate_page(page)


if __name__ == "__main__":
    unittest.main(verbosity=2)
