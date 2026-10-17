#!/usr/bin/env python3
"""End-to-end subprocess smoke checks against a neutral, private mock CLI."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

from run_tests import offline_environment

ROOT = Path(__file__).resolve().parents[1]
MOCK = ROOT / "dev/mock_pikpak/mock_pikpak.py"


def run(script, *args):
    return subprocess.run([sys.executable, str(ROOT / script), *args],
                          capture_output=True, text=True, timeout=20)


def smoke():
    checks = []
    with offline_environment() as private:
        fake = Path(os.environ["PIKPAK_BIN"])
        fake.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + " "
                        + shlex.quote(str(MOCK)) + ' "$@"\n')
        fake.chmod(0o700)
        ad_path, dup_path = private / "ad.json", private / "dupes.json"
        ad_script = "skills/pikpak-ad-cleaner/scripts/scan_ads.py"
        dup_script = "skills/pikpak-dedup/scripts/analyze_dupes.py"
        for script, path in ((ad_script, ad_path), (dup_script, dup_path)):
            result = run(script, "--folder", "", "--json", str(path))
            if result.returncode != 0:
                raise RuntimeError("neutral mock CLI scan failed")
        ad, dup = json.loads(ad_path.read_text()), json.loads(dup_path.read_text())
        assert ad["source"] == "live" and ad["complete"] is True
        assert [len(ad[key]) for key in ("high", "review", "protected")] == [3, 2, 2]
        checks.append("ad_scan_via_mock_subprocess")
        assert dup["complete"] is True and len(dup["groups"]) == 1
        assert dup["groups"][0]["count"] == 3
        assert [item["id"] for item in dup["groups"][0]["trash_candidates"]] == ["f_s1", "f_s3"]
        checks.append("version_candidates_via_mock_subprocess")

        approved = run(ad_script, "--from-report", str(ad_path), "--report-sha256",
                       hashlib.sha256(ad_path.read_bytes()).hexdigest(),
                       "--approve-id", "f_url", "--emit-trash-cmd", "high")
        assert approved.returncode == 0 and approved.stdout.strip() == "pikpak rm f_url"
        approved = run(dup_script, "--from-report", str(dup_path), "--report-sha256",
                       hashlib.sha256(dup_path.read_bytes()).hexdigest(),
                       "--approve-id", "f_s1", "--approve-id", "f_s3", "--emit-trash-cmd")
        assert approved.returncode == 0 and approved.stdout.strip() == "pikpak rm f_s1 f_s3"
        checks.append("hash_bound_explicit_approval_print_only")

        fake.write_text("#!/bin/sh\nprintf '%s\\n' 'UNAUTHENTICATED' >&2\nexit 1\n")
        failure_path = private / "failed-scan.json"
        failure = run(ad_script, "--folder", "", "--json", str(failure_path))
        failure_report = json.loads(failure_path.read_text())
        # Raw CLI streams are deliberately suppressed; a failed scan must be
        # nonzero, incomplete and explicitly classified, never a cleanup preview.
        assert failure.returncode == 2
        assert failure_report["complete"] is False
        assert failure_report["error_code"] == "CLI_FAILED"
        assert failure_report["cli_exit_code"] == 1
        assert failure.stdout.strip() == "" and "CLI_FAILED" in failure.stderr
        assert "UNAUTHENTICATED" not in failure.stderr + json.dumps(failure_report)
        checks.append("cli_failure_is_nonzero_incomplete_and_sanitized")
    return {"passed": True, "transport": "neutral_mock_cli", "checks": checks}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        result = smoke()
    except (AssertionError, OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"passed": False, "error_type": type(exc).__name__}))
        return 1
    if args.json:
        print(json.dumps(result))
    else:
        print(f"全部 {len(result['checks'])} 项冒烟检查通过（仅中性假 CLI）。")
        for check in result["checks"]:
            print("- " + check)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
