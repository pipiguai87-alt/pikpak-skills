"""Regression tests for direct isolated execution of repository harnesses."""

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEV = ROOT / "dev"


class TestHarnessEntrypoints(unittest.TestCase):
    def test_smoke_entrypoint_works_under_isolated_python(self):
        """The smoke script must run under -I, which omits the script directory."""
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                str(DEV / "smoke_e2e.py"),
                "--json",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=90,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload.get("passed"), payload)

    def test_smoke_entrypoint_imports_sibling_without_inherited_dev_path(self):
        """The entry point must add its own directory before importing run_tests.

        Aggregate runs launch approved children through offline_bootstrap, which
        pre-inserts dev/ into sys.path, so the subprocess test above cannot detect
        a missing self-import fix there. This probe removes dev/ and any cached
        run_tests module before executing the entry point in-process.
        """
        original_path = list(sys.path)
        missing = object()
        cached = {name: sys.modules.get(name, missing) for name in ("run_tests", "smoke_entry_probe")}
        try:
            sys.path[:] = [entry for entry in sys.path
                           if Path(entry or Path.cwd()).resolve() != DEV]
            sys.modules.pop("run_tests", None)
            spec = importlib.util.spec_from_file_location("smoke_entry_probe", DEV / "smoke_e2e.py")
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        finally:
            sys.path[:] = original_path
            for name, value in cached.items():
                if value is missing:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = value
        self.assertTrue(callable(module.smoke))


if __name__ == "__main__":
    unittest.main()
