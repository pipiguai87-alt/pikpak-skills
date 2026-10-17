"""Regression checks for the mock CLI contract, never a real CLI."""
from pathlib import Path
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MOCK = ROOT / "dev/mock_pikpak/mock_pikpak.py"


def runner_module():
    spec = importlib.util.spec_from_file_location("offline_test_runner", ROOT / "dev/run_tests.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestGuardAttributeRestoration(unittest.TestCase):
    def test_automation_setup_does_not_poison_offline_guard_real_popen(self):
        import subprocess
        from unittest.mock import patch
        spec = importlib.util.spec_from_file_location(
            "automation_guard_import_order_probe", ROOT / "dev/test_maturity_automation.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        previous_guard = sys.modules.pop("offline_guard", None)
        real_popen = subprocess.Popen
        try:
            with patch.dict(sys.modules, {"automation_guard_import_order_probe": module}):
                spec.loader.exec_module(module)
                with module.safe_offline_environment():
                    guard = sys.modules.get("offline_guard")
                    assert guard is not None
                    self.assertIs(guard._REAL_POPEN, real_popen)
        finally:
            sys.modules.pop("offline_guard", None)
            if previous_guard is not None:
                sys.modules["offline_guard"] = previous_guard

    def test_automation_guard_restores_an_inherited_home_descriptor(self):
        from unittest.mock import patch
        spec = importlib.util.spec_from_file_location(
            'automation_guard_inheritance_probe', ROOT / 'dev/test_maturity_automation.py')
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        class NeutralBase:
            @classmethod
            def home(cls):
                return 'neutral-base-value'

        class NeutralPath(NeutralBase):
            pass

        descriptor = vars(NeutralBase)['home']
        self.assertNotIn('home', vars(NeutralPath))
        with patch.object(module, 'Path', NeutralPath):
            with module.safe_offline_environment():
                with self.assertRaisesRegex(AssertionError, 'automation tests forbid'):
                    NeutralPath.home()
        self.assertNotIn('home', vars(NeutralPath))
        self.assertIs(vars(NeutralBase)['home'], descriptor)
        self.assertEqual(NeutralPath.home(), 'neutral-base-value')


class TestOfflineGuardBoundaries(unittest.TestCase):
    """Negative probes are synthetic; real children use only the neutral probe."""
    def test_unapproved_process_entries_are_denied(self):
        runner = runner_module()
        with runner.offline_environment():
            for executable, argv in [
                ("/unexecuted/pikpak", ["/unexecuted/pikpak", "auth", "status"]),
                ("/bin/sh", ["/bin/sh", "-c", "SYNTHETIC_NOT_EXECUTED"]),
                ("/usr/bin/env", ["/usr/bin/env", sys.executable, "-c", "pass"]),
                (sys.executable, [sys.executable, "-c", "pass"]),
                (sys.executable, [sys.executable, str(MOCK), "ls", "--raw"]),
                (sys.executable, [sys.executable, "-m", "http.server"]),
            ]:
                with self.subTest(argv=argv), self.assertRaises(PermissionError):
                    sys.audit("subprocess.Popen", executable, argv, None, dict(os.environ))

    def test_exec_spawn_udp_and_resolver_escapes_are_denied(self):
        runner = runner_module()
        with runner.offline_environment():
            events = [
                ("os.system", (b"SYNTHETIC_NOT_EXECUTED",)),
                ("os.exec", (sys.executable, [sys.executable], dict(os.environ))),
                ("os.posix_spawn", (sys.executable, [sys.executable], dict(os.environ))),
                ("os.spawn", (0, sys.executable, [sys.executable], dict(os.environ))),
                ("socket.sendto", (None, ("192.0.2.1", 443))),
                ("socket.sendmsg", (None, ("192.0.2.1", 443))),
                ("socket.getaddrinfo", ("example.invalid", 443, 0, 0, 0)),
                ("socket.gethostbyname", ("example.invalid",)),
            ]
            for event, args in events:
                with self.subTest(event=event), self.assertRaises(PermissionError):
                    sys.audit(event, *args)

    def test_critical_child_environment_cannot_override_the_guard(self):
        runner = runner_module()
        with runner.offline_environment():
            for key in ("HOME", "PATH", "PIKPAK_BIN", "PYTHONPATH", "PYTHONHOME"):
                env = dict(os.environ, **{key: "/synthetic/unapproved"})
                with self.subTest(key=key), self.assertRaises(PermissionError):
                    sys.audit("subprocess.Popen", sys.executable,
                              [sys.executable, str(ROOT / "dev/offline_child_probe.py")], None, env)

    def test_hostname_alias_and_reverse_dns_are_not_offline_exceptions(self):
        runner = runner_module()
        with runner.offline_environment():
            for event, args in (("socket.connect", (None, ("localhost", 443))),
                                ("socket.getaddrinfo", ("localhost", 443, 0, 0, 0)),
                                ("socket.getnameinfo", (("192.0.2.1", 443), 0)),
                                ("socket.gethostbyaddr", ("127.0.0.2",))):
                with self.subTest(event=event), self.assertRaises(PermissionError):
                    sys.audit(event, *args)

    def test_private_fake_cli_inherits_the_same_child_audit(self):
        runner = runner_module()
        with runner.offline_environment() as private:
            fake = Path(os.environ["PIKPAK_BIN"])
            fake.write_text("#!" + sys.executable + "\n" + (ROOT / "dev/offline_child_probe.py").read_text())
            completed = subprocess.run([str(fake)], capture_output=True, text=True, timeout=10)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(report["allowed"], [])
        self.assertFalse(report["network_attempted"])
        self.assertEqual(len(report["denied"]), 6)

    def test_real_launcher_rejects_environment_override_before_a_process_starts(self):
        from unittest.mock import patch
        runner = runner_module()
        import offline_guard
        with runner.offline_environment(), \
                patch.object(offline_guard, "_REAL_POPEN", side_effect=AssertionError("no real process in negative tests")) as raw:
            for key in ("HOME", "PATH", "PIKPAK_BIN", "PYTHONPATH", "PYTHONHOME", "TMPDIR"):
                env = dict(os.environ, **{key: "/synthetic/unapproved"})
                with self.subTest(key=key), self.assertRaises(PermissionError):
                    subprocess.run([sys.executable, str(ROOT / "dev/offline_child_probe.py")], env=env)
            raw.assert_not_called()

    def test_real_launcher_rejects_wrappers_and_interpreter_controls_before_spawn(self):
        from unittest.mock import patch
        runner = runner_module()
        import offline_guard
        worker = str(ROOT / "skills/pikpak-speed-guide/scripts/speed_bench.py")
        cases = [
            (["/unexecuted/pikpak", "ls"], {}),
            (["/bin/sh", "-c", "SYNTHETIC_NOT_EXECUTED"], {}),
            (["/usr/bin/env", sys.executable, worker, "--worker"], {}),
            ([sys.executable, "-c", "SYNTHETIC_NOT_EXECUTED"], {}),
            ([sys.executable, "-m", "http.server"], {}),
            ([sys.executable, worker, "--worker", "extra"], {}),
            ([sys.executable, "skills/pikpak-speed-guide/scripts/speed_bench.py", "--worker"], {}),
            ([sys.executable, worker, "--worker"], {"shell": True}),
            ([sys.executable, worker, "--worker"], {"executable": sys.executable}),
        ]
        with runner.offline_environment(), \
                patch.object(offline_guard, "_REAL_POPEN", side_effect=AssertionError("negative probes cannot spawn")) as raw:
            for argv, controls in cases:
                with self.subTest(argv=argv, controls=controls), self.assertRaises(PermissionError):
                    subprocess.Popen(argv, **controls)
            raw.assert_not_called()

    def test_approved_child_disables_site_startup_before_bootstrap(self):
        from unittest.mock import patch
        runner = runner_module()
        import offline_guard
        with runner.offline_environment(), patch.object(offline_guard, "_REAL_POPEN") as raw:
            subprocess.Popen([sys.executable, str(ROOT / "dev/offline_child_probe.py")])
            raw.assert_called_once()
            self.assertEqual(raw.call_args.args[0][:4], [sys.executable, "-I", "-B", "-S"])

    def test_controlled_child_inherits_offline_audit(self):
        runner = runner_module()
        with runner.offline_environment():
            completed = subprocess.run([sys.executable, str(ROOT / "dev/offline_child_probe.py")],
                                       capture_output=True, text=True, timeout=10)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertFalse(report["network_attempted"])
        self.assertEqual(report["allowed"], [])
        self.assertEqual(set(report["denied"]), {"dns", "udp", "shell", "python", "exec", "env"})


class TestMockContract(unittest.TestCase):
    def test_discovered_module_cleanups_run_before_the_next_module(self):
        from types import ModuleType
        from unittest.mock import patch
        runner = runner_module()
        events = []
        first = ModuleType("synthetic_offline_first")
        second = ModuleType("synthetic_offline_second")

        def setup():
            events.append("opened")
            unittest.addModuleCleanup(events.append, "closed")

        setattr(first, "setUpModule", setup)

        class First(unittest.TestCase):
            def test_active(self):
                self.assertEqual(events, ["opened"])

        class Second(unittest.TestCase):
            def test_cleaned(self):
                self.assertEqual(events, ["opened", "closed"])

        First.__module__, Second.__module__ = first.__name__, second.__name__
        discovered = unittest.TestSuite([First("test_active"), Second("test_cleaned")])
        loader = type("NeutralDiscovery", (), {"discover": lambda _, *a, **kw: discovered})()
        import io
        output = io.StringIO()
        with patch.dict(sys.modules, {first.__name__: first, second.__name__: second}), \
                patch.object(unittest.case, "_module_cleanups", []):
            suite = runner.load_tests(loader, unittest.TestSuite(), None)
            result = unittest.TextTestRunner(stream=output).run(suite)
            # Only the synthetic module's callbacks, never the outer test guard.
            unittest.case.doModuleCleanups()
        self.assertTrue(result.wasSuccessful(), output.getvalue())

    def test_full_runner_isolation_blocks_real_cli_and_network(self):
        runner = runner_module()
        import socket
        self.assertTrue(hasattr(runner, "offline_environment"), "full runner has no isolation gate")
        old_home = os.environ.get("HOME")
        with runner.offline_environment() as private:
            self.assertNotEqual(os.environ.get("HOME"), old_home)
            self.assertTrue(Path(os.environ["TMPDIR"]).is_relative_to(private))
            proc = subprocess.run([os.environ["PIKPAK_BIN"], "task", "rm", "SYNTHETIC"],
                                  capture_output=True, text=True, timeout=10)
            self.assertEqual(proc.returncode, 97)
            with self.assertRaises(PermissionError):
                socket.getaddrinfo("example.invalid", 443)
        self.assertEqual(os.environ.get("HOME"), old_home)

    def test_full_runner_discovers_every_regression_module(self):
        runner = runner_module()
        with runner.offline_environment():
            suite = runner.load_tests(unittest.defaultTestLoader, unittest.TestSuite(), None)
            def modules(tests):
                names = set()
                for item in tests:
                    if isinstance(item, unittest.TestSuite):
                        names.update(modules(item))
                    else:
                        names.add(type(item).__module__.split(".")[-1])
                return names
            expected = {path.stem for path in (ROOT / "dev").glob("test_*.py")}
            self.assertEqual(modules(suite), expected)

    def test_smoke_forces_mock_even_with_an_inherited_cli_override(self):
        runner = runner_module()
        with runner.offline_environment() as private:
            trap = private / "outside-cli"
            marker = private / "escaped"
            trap.write_text("#!/bin/sh\ntouch " + str(marker) + "\nexit 98\n")
            trap.chmod(0o700)
            env = dict(os.environ, PIKPAK_BIN=str(trap))
            completed = subprocess.run([sys.executable, str(ROOT / "dev/smoke_e2e.py"), "--json"],
                                       env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(completed.returncode, 0, completed.stdout)
            self.assertTrue(json.loads(completed.stdout)["passed"])
            self.assertFalse(marker.exists())

    def test_smoke_launcher_has_no_shared_temp_paths(self):
        self.assertNotIn("/tmp/", (ROOT / "dev/smoke_e2e.sh").read_text())

    def test_unknown_fixture_pages_are_not_empty_success(self):
        for args in (["-p", "missing-folder"], ["-t", "missing-page"]):
            with self.subTest(args=args):
                completed = subprocess.run([sys.executable, str(MOCK), "ls", "--raw", *args],
                                           capture_output=True, text=True, timeout=10)
                self.assertNotEqual(completed.returncode, 0)

    def test_unknown_mock_flags_are_rejected(self):
        completed = subprocess.run([sys.executable, str(MOCK), "ls", "--made-up-flag"],
                                   capture_output=True, text=True, timeout=10)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("不支持", completed.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
