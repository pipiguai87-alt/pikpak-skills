#!/usr/bin/env python3
"""Automation maturity regressions: stdlib, synthetic data, no external APIs.
Run: python3 dev/test_maturity_automation.py
"""
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import socket
import sys
import tempfile
import threading
import unittest
import urllib.request
from contextlib import contextmanager, redirect_stdout
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def safe_offline_environment():
    """Hard-deny real effects before importing any tested module.

    Direct replacement intentionally survives legacy tests' patch.stopall().
    Individual tests may temporarily supply synthetic responses, never effects.
    """
    def forbidden(*args, **kwargs):
        raise AssertionError("automation tests forbid real process/network/home/notification")

    targets = [(subprocess, "run"), (subprocess, "Popen"),
               (socket, "socket"), (socket, "create_connection"), (socket, "getaddrinfo"),
               (urllib.request, "urlopen"), (os, "system"), (Path, "home")]
    missing = object()
    # Class descriptors may be inherited (Path.home on newer Python). Preserve
    # attribute ownership so exit removes our override instead of shadowing it.
    originals = [(obj, name, vars(obj).get(name, missing)) for obj, name in targets]
    try:
        # Import the runner and its guard before installing the direct forbidden
        # hooks. offline_guard._REAL_POPEN must capture the real launcher.
        spec = importlib.util.spec_from_file_location("automation_offline_harness", ROOT / "dev/run_tests.py")
        assert spec and spec.loader
        harness = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(harness)
        for obj, name in targets:
            setattr(obj, name, staticmethod(forbidden) if obj is Path else forbidden)
        with harness.offline_environment() as private:
            # Shared isolation may install its own process guards. Reassert our
            # stricter no-process/no-network boundary after it, without relying
            # on the parent's child-process policy or patch registration.
            for obj, name in targets:
                setattr(obj, name, staticmethod(forbidden) if obj is Path else forbidden)
            # Never inherit a real token, account selector, endpoint or old state.
            os.environ.update({"TG_BOT_TOKEN": "123:FAKE_SECRET",
                               "TG_API_BASE": "https://api.telegram.org",
                               "PIKPAK_ALLOWED_CHAT_IDS": "11",
                               "PIKPAK_ALLOWED_USER_IDS": "11",
                               "PIKPAK_ACCOUNT_SCOPE": "fixture-account",
                               "PIKPAK_AUTOMATION_STATE_DIR": str(private / "automation")})
            yield private
    finally:
        for obj, name, original in reversed(originals):
            if original is missing:
                delattr(obj, name)
            else:
                setattr(obj, name, original)


def setUpModule():
    global _offline_context
    _offline_context = safe_offline_environment()
    _offline_context.__enter__()


def tearDownModule():
    _offline_context.__exit__(None, None, None)


def module(skill, name):
    path = ROOT / "skills" / skill / "scripts" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name + "_maturity", path)
    assert spec and spec.loader
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class EnvelopeTests(unittest.TestCase):
    def test_direct_guards_survive_stopall_and_forbid_every_real_effect(self):
        patch.stopall()
        operations = [lambda: subprocess.run(["never-execute"]),
                      lambda: subprocess.Popen(["never-execute"]),
                      lambda: socket.socket(),
                      lambda: socket.create_connection(("example.test", 443)),
                      lambda: socket.getaddrinfo("example.test", 443),
                      lambda: urllib.request.urlopen("https://example.test/never"),
                      lambda: os.system("never-execute"), lambda: Path.home()]
        for operation in operations:
            with self.assertRaisesRegex(AssertionError, "automation tests forbid"):
                operation()
        self.assertTrue(Path(tempfile.gettempdir()).is_relative_to(Path(os.environ["HOME"]).parent))
        self.assertEqual(os.environ["TG_BOT_TOKEN"], "123:FAKE_SECRET")
        guard = module("pikpak-task-guardian", "task_guardian")
        with patch.object(guard.sys, "platform", "darwin"), self.assertRaisesRegex(AssertionError, "automation tests forbid"):
            guard.notify("fixture", "fixture")


class PrivateFileBoundaryTests(unittest.TestCase):
    def test_guardian_rejects_existing_nonprivate_json(self):
        guard = module("pikpak-task-guardian", "task_guardian")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sources.json"
            path.write_text("[]", encoding="utf-8")
            path.chmod(0o644)
            with self.assertRaisesRegex(ValueError, "0600"):
                guard.load_json(path, [])

    def test_telegram_rejects_existing_nonprivate_state_files(self):
        bot = module("pikpak-telegram-bot", "tg_bot")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pikpak = bot.PikPakStore(root, "fixture-account")
            pikpak.path.write_text(json.dumps({"scope": pikpak.scope,
                                                "cooldown_until": 0,
                                                "failures": 0}), encoding="utf-8")
            pikpak.path.chmod(0o644)
            with self.assertRaisesRegex(ValueError, "0600"):
                pikpak.read()

            updates = bot.UpdateStore(root, "fixture-account", "123:FAKE_SECRET")
            updates.path.write_text(json.dumps({
                "scope": updates.scope, "receipts": {}, "messages": {},
                "duplicate_messages": {}, "next_offset": None, "epoch": 0,
                "last_observed_at": None, "idle_resync_seconds": bot.IDLE_RESYNC_SECONDS,
                "resync_required": False,
            }), encoding="utf-8")
            updates.path.chmod(0o644)
            with self.assertRaisesRegex(ValueError, "0600"):
                updates.read()

    def test_private_files_reject_special_modes_symlinks_and_hardlinks(self):
        bot = module("pikpak-telegram-bot", "tg_bot")
        guard = module("pikpak-task-guardian", "task_guardian")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for mode in (0o1600, 0o2600, 0o4600):
                with self.subTest(mode=oct(mode)):
                    source = root / ("source-" + oct(mode)[2:])
                    source.write_text("[]", encoding="utf-8")
                    source.chmod(mode)
                    with self.assertRaisesRegex(ValueError, "0600"):
                        guard.load_json(source, [])
                    pikpak = bot.PikPakStore(root, "fixture-account")
                    pikpak.path.write_text(json.dumps({"scope": pikpak.scope,
                                                        "cooldown_until": 0,
                                                        "failures": 0}), encoding="utf-8")
                    pikpak.path.chmod(mode)
                    with self.assertRaisesRegex(ValueError, "0600"):
                        pikpak.read()
                    updates = bot.UpdateStore(root, "fixture-account", "fixture-bot")
                    updates.path.write_text(json.dumps({
                        "scope": updates.scope, "receipts": {}, "messages": {},
                        "duplicate_messages": {}, "next_offset": None, "epoch": 0,
                        "last_observed_at": None, "idle_resync_seconds": bot.IDLE_RESYNC_SECONDS,
                        "resync_required": False}), encoding="utf-8")
                    updates.path.chmod(mode)
                    with self.assertRaisesRegex(ValueError, "0600"):
                        updates.read()
                    transport = bot.TransportStore(updates)
                    transport.path.write_text(json.dumps({"scope": updates.scope,
                                                          "cooldown_until": 0,
                                                          "failures": 0, "fatal": False}), encoding="utf-8")
                    transport.path.chmod(mode)
                    with self.assertRaisesRegex(ValueError, "0600"):
                        transport.read()
            target = root / "target.json"
            target.write_text("[]", encoding="utf-8")
            target.chmod(0o600)
            symlink = root / "symlink.json"
            symlink.symlink_to(target)
            with self.assertRaisesRegex(ValueError, "0600"):
                guard.load_json(symlink, [])
            hardlink = root / "hardlink.json"
            os.link(target, hardlink)
            with self.assertRaisesRegex(ValueError, "0600"):
                guard.load_json(hardlink, [])


class BotTests(unittest.TestCase):
    def setUp(self):
        self.bot = module("pikpak-telegram-bot", "tg_bot")
        self.tg = Mock()
        self.tg.send.return_value = {"message_id": 1}
        self.env = patch.dict(os.environ, {"PIKPAK_ALLOWED_USER_IDS": "11"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.cli = patch.object(self.bot, "pikpak_json", return_value=({"task_id": "T1"}, None)).start()
        patch.object(self.bot.subprocess, "run", side_effect=AssertionError("unmocked process forbidden")).start()
        patch.object(self.bot.urllib.request, "urlopen", side_effect=AssertionError("unmocked network forbidden")).start()
        self.addCleanup(patch.stopall)

    def msg(self, user=11, **extra):
        return {"chat": {"id": -100, "type": "supergroup"},
                "from": {"id": user, "is_bot": False}, "text": "/save https://example.test/file", **extra}

    def test_chat_whitelist_does_not_authorize_other_group_members(self):
        self.bot.process_message(self.tg, self.msg(user=22), {"-100"})
        self.cli.assert_not_called()
        self.tg.send.assert_not_called()

    def test_explicit_group_sender_is_authorized(self):
        self.bot.process_message(self.tg, self.msg(), {"-100"})
        self.cli.assert_called_once()

    def test_anonymous_sender_chat_is_rejected(self):
        self.bot.process_message(self.tg, self.msg(sender_chat={"id": -100}), {"-100"})
        self.cli.assert_not_called()

    def test_private_chat_requires_matching_sender(self):
        msg = {"chat": {"id": 11, "type": "private"}, "from": {"id": 22}, "text": "/help"}
        with patch.dict(os.environ, {"PIKPAK_ALLOWED_USER_IDS": "22"}):
            self.bot.process_message(self.tg, msg, {"11"})
        self.tg.send.assert_not_called()

    def test_private_chat_owner_can_submit_without_group_user_list(self):
        msg = {"chat": {"id": 11, "type": "private"}, "from": {"id": 11}, "text": "/save https://example.test/f"}
        with patch.dict(os.environ, {"PIKPAK_ALLOWED_USER_IDS": ""}):
            self.bot.process_message(self.tg, msg, {"11"})
        self.cli.assert_called_once()

    def test_save_rejects_cli_flag_instead_of_url(self):
        self.bot.handle_save(self.tg, 11, "/save --help")
        self.cli.assert_not_called()

    def test_other_bot_sender_is_rejected(self):
        msg = self.msg()
        msg["from"]["is_bot"] = True
        self.bot.process_message(self.tg, msg, {"-100"})
        self.cli.assert_not_called()

    def test_missing_sender_is_rejected(self):
        msg = self.msg()
        del msg["from"]
        self.bot.process_message(self.tg, msg, {"-100"})
        self.cli.assert_not_called()

    def test_token_url_exception_is_not_logged(self):
        client = self.bot.Telegram("123:FAKE_SECRET")
        out = io.StringIO()
        with patch.object(self.bot.urllib.request, "urlopen", side_effect=OSError(client.base)), redirect_stdout(out):
            self.assertIsNone(client.get_updates())
        self.assertNotIn("FAKE_SECRET", out.getvalue())

    def test_http_429_keeps_retry_after_without_logging_body(self):
        client = self.bot.Telegram("123:FAKE_SECRET")
        error = self.bot.urllib.error.HTTPError(client.base, 429, "FAKE_SECRET", {}, io.BytesIO(b'{"parameters":{"retry_after":7},"description":"FAKE_SECRET"}'))
        out = io.StringIO()
        with patch.object(self.bot.urllib.request, "urlopen", side_effect=error), redirect_stdout(out):
            self.assertIsNone(client.get_updates())
        self.assertEqual(client.retry_after, 7)
        self.assertNotIn("FAKE_SECRET", out.getvalue())

    def test_bad_telegram_json_is_contained(self):
        client = self.bot.Telegram("123:FAKE_SECRET")
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'not json FAKE_SECRET'
        out = io.StringIO()
        with patch.object(self.bot.urllib.request, "urlopen", return_value=response), redirect_stdout(out):
            self.assertIsNone(client.get_updates())
        self.assertNotIn("FAKE_SECRET", out.getvalue())

    def test_failed_response_never_announces_success(self):
        self.cli.return_value = ({"error": "rejected"}, None)
        self.bot.handle_save(self.tg, "11", "/save https://example.test/file")
        text = "\n".join(c.args[1] for c in self.tg.send.call_args_list)
        self.assertNotIn("已开始转存", text)
        self.assertNotIn("已提交", text)

    def test_save_metadata_only_exports_bounded_noncredential_strings(self):
        invalid = [None, True, 42, {"access_token": "SYNTHETIC_SAVE_SECRET"},
                   [{"nested": {"access_token": "SYNTHETIC_SAVE_SECRET"}}],
                   "access_token=SYNTHETIC_SAVE_SECRET", "password: SYNTHETIC_SAVE_SECRET",
                   "https://example.test/f?api_key=SYNTHETIC_SAVE_SECRET",
                   "x" * 121, "name\nSYNTHETIC_SAVE_SECRET"]
        for name in ["fixture.mp4", *invalid]:
            with self.subTest(name=name):
                self.cli.return_value = ({"task_id": "T1", "file_name": name,
                                          "access_token": "SYNTHETIC_TOP_LEVEL_SECRET"}, None)
                self.tg.send.reset_mock()
                result = self.bot.process_message(self.tg, self.msg(), {"-100"})
                self.assertEqual(result, "submitted")
                text = "\n".join(call.args[1] for call in self.tg.send.call_args_list)
                self.assertIn("文件名：" + ("fixture.mp4" if name == "fixture.mp4" else "unknown"), text)
                self.assertIn("任务 ID：T1", text)
                self.assertNotIn("SYNTHETIC", text)
                self.assertTrue(all(call.args[0] == "-100" for call in self.tg.send.call_args_list))
                self.assertLessEqual(len(text), 4000)

    def test_save_error_metadata_is_fixed_without_raw_error_text(self):
        secret = "SYNTHETIC_SAVE_ERROR_SECRET"
        self.cli.return_value = (None, self.bot.PikPakFailure("unknown", message=secret))
        result = self.bot.process_message(self.tg, self.msg(), {"-100"})
        self.assertEqual(result, "submission_unknown")
        text = "\n".join(call.args[1] for call in self.tg.send.call_args_list)
        self.assertNotIn(secret, text)
        self.assertIn("请人工核对，不要直接重发", text)

    def test_tasks_and_status_metadata_fail_closed_without_stringifying_containers(self):
        secret = "SYNTHETIC_TASK_SECRET"
        payload = {"tasks": [{"name": {"access_token": secret},
                              "status": [secret], "progress": {"secret": secret},
                              "id": [secret]}]}
        with patch.object(self.bot, "pikpak_json", return_value=(payload, None)):
            self.bot.handle_tasks(self.tg, "11")
        text = self.tg.send.call_args.args[1]
        self.assertIn("• unknown", text)
        self.assertNotIn(secret, text)
        self.tg.reset_mock()
        with patch.object(self.bot, "pikpak_json", return_value=({
                "status": {"access_token": secret}, "progress": [secret],
                "file_name": {"nested": secret}}, None)):
            self.bot.handle_status(self.tg, "11", "task-1")
        text = self.tg.send.call_args.args[1]
        self.assertIn("状态：unknown", text)
        self.assertIn("进度：unknown", text)
        self.assertIn("文件名：unknown", text)
        self.assertNotIn(secret, text)

    def test_error_json_is_not_success(self):
        with patch.object(self.bot, "pikpak", return_value=(0, '{"error":"rejected"}', "")):
            # Call the actual wrapper, not the command-dispatch mock.
            other = module("pikpak-telegram-bot", "tg_bot")
            with patch.object(other, "pikpak", return_value=(0, '{"error":"rejected"}', "")):
                data, err = other.pikpak_json("task", "add", "fixture")
            self.assertIsNotNone(err)

    @staticmethod
    def response(payload):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = json.dumps(payload).encode()
        return response

    def test_send_errors_stop_batch_before_claiming_next_update(self):
        for code in (429, 403):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as folder:
                store = self.bot.UpdateStore(Path(folder), "fixture", "bot")
                client = self.bot.Telegram("123:FAKE_SECRET")
                error = self.bot.urllib.error.HTTPError(client.base, code, "fake", {},
                    io.BytesIO(b'{"parameters":{"retry_after":30}}'))
                updates = [{"update_id": n, "message": self.msg(message_id=n)} for n in (1, 2)]
                with patch.object(self.bot.time, "time", return_value=1000), \
                        patch.object(self.bot.urllib.request, "urlopen", side_effect=[error,
                            self.response({"ok": True, "result": {"message_id": 1}})]) as network:
                    self.assertEqual(self.bot.process_updates(client, updates, {"-100"}, store), 2)
                self.assertEqual(network.call_count, 1)
                self.assertEqual(store.read()["receipts"], {"1": "uncertain"})
                self.assertEqual(store.read()["messages"], {"-100:1": "1"})
                self.cli.assert_not_called()

    def test_all_telegram_methods_share_durable_cooldown_and_fatal_state(self):
        for code in (429, 403):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as folder:
                store = self.bot.UpdateStore(Path(folder), "fixture", "123:FAKE_SECRET")
                with patch.object(self.bot.time, "time", return_value=1000):
                    client = self.bot.Telegram("123:FAKE_SECRET", store=store)
                    error = self.bot.urllib.error.HTTPError(client.base, code, "fake", {},
                        io.BytesIO(b'{"parameters":{"retry_after":30}}'))
                    with patch.object(self.bot.urllib.request, "urlopen", side_effect=error) as network:
                        with self.assertRaises(RuntimeError):
                            client.send(11, "fixture")
                        again = self.bot.Telegram("123:ROTATED_FAKE", store=store)
                        self.assertIsNone(again.get_updates(2))
                        with self.assertRaises(RuntimeError):
                            again.send(11, "fixture")
                        self.assertEqual(network.call_count, 1)
                    self.assertNotIn("FAKE", "".join(p.read_text() for p in Path(folder).glob("*.json")))
                with patch.object(self.bot.time, "time", return_value=1031), \
                        patch.object(self.bot.urllib.request, "urlopen", return_value=self.response({"ok": True, "result": []})) as network:
                    again = self.bot.Telegram("123:FAKE_SECRET", store=store)
                    self.assertEqual(again.get_updates(2), [] if code == 429 else None)
                    self.assertEqual(network.call_count, 1 if code == 429 else 0)

    def test_main_send_429_waits_then_processes_only_unclaimed_messages(self):
        with tempfile.TemporaryDirectory() as folder:
            clock, methods = [1000], []
            updates = [{"update_id": n, "message": self.msg(message_id=n)} for n in (1, 2)]
            def request(req, **kwargs):
                method = req.full_url.rsplit("/", 1)[1]
                params = self.bot.urllib.parse.parse_qs(req.data.decode())
                methods.append((method, clock[0], params))
                number = len(methods)
                if number == 1:
                    return self.response({"ok": True, "result": updates})
                if number == 3:
                    raise self.bot.urllib.error.HTTPError(req.full_url, 429, "fake", {},
                        io.BytesIO(b'{"parameters":{"retry_after":30}}'))
                if number == 4:
                    self.assertGreaterEqual(clock[0], 1030)
                    self.assertEqual(params["offset"], ["2"])
                    return self.response({"ok": True, "result": updates})
                if number == 7:
                    raise KeyboardInterrupt
                return self.response({"ok": True, "result": {"message_id": number}})
            with patch.dict(os.environ, {"PIKPAK_AUTOMATION_STATE_DIR": folder, "PIKPAK_ALLOWED_CHAT_IDS": "-100"}), \
                    patch.object(self.bot.sys, "argv", ["bot"]), \
                    patch.object(self.bot, "pikpak", return_value=(0, "fixture-version", "")), \
                    patch.object(self.bot.time, "time", side_effect=lambda: clock[0]), \
                    patch.object(self.bot.time, "sleep", side_effect=lambda delay: clock.__setitem__(0, clock[0] + delay)) as sleep, \
                    patch.object(self.bot.urllib.request, "urlopen", side_effect=request):
                with self.assertRaises(KeyboardInterrupt):
                    self.bot.main()
            self.assertEqual(self.cli.call_count, 2)
            self.assertEqual(sleep.call_args_list[0].args, (30,))
            store = self.bot.UpdateStore(Path(folder), "fixture-account", "123:FAKE_SECRET")
            self.assertEqual(store.read()["receipts"], {"1": "uncertain", "2": "submitted"})
            self.assertEqual(store.read()["next_offset"], 3)
            self.assertEqual([m[0] for m in methods], ["getUpdates", "sendMessage", "sendMessage", "getUpdates", "sendMessage", "sendMessage", "getUpdates"])
            self.assertTrue(store.path.with_suffix(".transport.json").exists())

    def test_main_send_fatal_exits_without_second_claim_or_poll(self):
        with tempfile.TemporaryDirectory() as folder:
            updates = [{"update_id": n, "message": self.msg(message_id=n)} for n in (1, 2)]
            error = self.bot.urllib.error.HTTPError("https://example.test/fake", 403, "fake", {}, io.BytesIO(b'{}'))
            with patch.dict(os.environ, {"PIKPAK_AUTOMATION_STATE_DIR": folder, "PIKPAK_ALLOWED_CHAT_IDS": "-100"}), \
                    patch.object(self.bot.sys, "argv", ["bot"]), \
                    patch.object(self.bot, "pikpak", return_value=(0, "fixture-version", "")), \
                    patch.object(self.bot.time, "sleep") as sleep, \
                    patch.object(self.bot.urllib.request, "urlopen", side_effect=[self.response({"ok": True, "result": updates}), error]) as network:
                with self.assertRaises(SystemExit) as stopped:
                    self.bot.main()
                self.assertNotEqual(stopped.exception.code, 0)
                self.assertEqual(network.call_count, 2)
                sleep.assert_not_called()
            store = self.bot.UpdateStore(Path(folder), "fixture-account", "123:FAKE_SECRET")
            self.assertEqual(store.read()["receipts"], {"1": "uncertain"})
            self.assertEqual(store.read()["next_offset"], 2)
            self.cli.assert_not_called()

    def test_delayed_message_generation_blocks_old_offset_in_full_main_loop(self):
        with tempfile.TemporaryDirectory() as folder:
            delayed = self.msg(message_id=9)
            delayed["date"] = 1000
            updates = [{"update_id": 900, "message": delayed}]
            with patch.dict(os.environ, {"PIKPAK_AUTOMATION_STATE_DIR": folder,
                                         "PIKPAK_ALLOWED_CHAT_IDS": "-100"}), \
                    patch.object(self.bot.sys, "argv", ["bot", "--once"]), \
                    patch.object(self.bot, "pikpak", return_value=(0, "fixture-version", "")), \
                    patch.object(self.bot.time, "time", return_value=83800), \
                    patch.object(self.bot.urllib.request, "urlopen", side_effect=[
                        self.response({"ok": True, "result": updates}),
                        self.response({"ok": True, "result": {"message_id": 1}}),
                        self.response({"ok": True, "result": {"message_id": 2}})]) as network:
                self.bot.main()
            store = self.bot.UpdateStore(Path(folder), "fixture-account", "123:FAKE_SECRET")
            self.assertEqual(store.read()["last_observed_at"], 1000)
            self.assertEqual(store.read()["next_offset"], 901)
            self.assertTrue(store.needs_resync(store.read(), 605801))
            with patch.dict(os.environ, {"PIKPAK_AUTOMATION_STATE_DIR": folder}), \
                    patch.object(self.bot.sys, "argv", ["bot", "--once"]), \
                    patch.object(self.bot.time, "time", return_value=605801), \
                    patch.object(self.bot.urllib.request, "urlopen",
                                 side_effect=AssertionError("must block before stale high offset")) as network:
                with self.assertRaisesRegex(ValueError, "resync"):
                    self.bot.main()
            network.assert_not_called()

    def test_duplicate_message_does_not_advance_generation_boundary(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.bot.UpdateStore(Path(folder), "fixture-account", "fixture-bot")
            first = self.msg(message_id=9)
            first["date"] = 1000
            duplicate = dict(first)
            with patch.object(self.bot.time, "time", return_value=2000):
                self.bot.process_updates(self.tg, [{"update_id": 1, "message": first}], {"-100"}, store)
            with patch.object(self.bot.time, "time", return_value=605000):
                self.bot.process_updates(self.tg, [{"update_id": 2, "message": duplicate}], {"-100"}, store)
            state = store.read()
            self.assertEqual(state["last_observed_at"], 1000)
            self.assertEqual(state["receipts"]["2"], "duplicate_message")

    def test_resync_uses_pending_generation_boundary_not_local_now(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.bot.UpdateStore(Path(folder), "fixture-account", "fixture-bot")
            old = self.msg(message_id=1)
            old["date"] = 1000
            with patch.object(self.bot.time, "time", return_value=1000):
                self.bot.process_updates(self.tg, [{"update_id": 900, "message": old}], {"-100"}, store)
            pending_message = self.msg(message_id=2)
            pending_message["date"] = 500000
            self.tg.get_updates.return_value = [{"update_id": 5, "message": pending_message}]
            with patch.object(self.bot.time, "time", return_value=605801):
                self.bot.resynchronize(self.tg, store)
            self.assertEqual(store.read()["last_observed_at"], 500000)

    def test_unknown_message_date_reserves_pending_age_before_old_offset(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.bot.UpdateStore(Path(folder), "fixture-account", "fixture-bot")
            with patch.object(self.bot.time, "time", return_value=83800):
                self.bot.process_updates(self.tg, [{"update_id": 900, "message": self.msg(message_id=1)}],
                                         {"-100"}, store)
            self.assertLessEqual(store.read()["last_observed_at"], 0)
            with patch.object(self.bot.time, "time", return_value=605801):
                with self.assertRaisesRegex(ValueError, "resync"):
                    store.prepare_poll()

    def test_week_idle_blocks_old_offset_then_manual_resync_preserves_message_ledger(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.bot.UpdateStore(Path(folder), "fixture-account", "123:FAKE_SECRET")
            old = {"update_id": 900, "message": self.msg(message_id=9)}
            with patch.object(self.bot.time, "time", return_value=1000):
                self.bot.process_updates(self.tg, [old], {"-100"}, store)
            pending = [{"update_id": 5, "message": self.msg(message_id=9)},
                       {"update_id": 6, "message": self.msg(message_id=10)}]
            with patch.dict(os.environ, {"PIKPAK_AUTOMATION_STATE_DIR": folder, "PIKPAK_ALLOWED_CHAT_IDS": "-100"}), \
                    patch.object(self.bot.time, "time", return_value=605801), \
                    patch.object(self.bot, "pikpak", return_value=(0, "fixture-version", "")):
                with patch.object(self.bot.sys, "argv", ["bot", "--once"]), \
                        patch.object(self.bot.urllib.request, "urlopen", side_effect=AssertionError("must block before old high offset")) as network:
                    with self.assertRaisesRegex(ValueError, "resync"):
                        self.bot.main()
                    network.assert_not_called()
                self.assertTrue(store.read()["resync_required"])
                def response(req, **kwargs):
                    params = self.bot.urllib.parse.parse_qs(req.data.decode())
                    self.assertNotIn("offset", params)
                    return self.response({"ok": True, "result": pending})
                with patch.object(self.bot.sys, "argv", ["bot", "--resync"]), \
                        patch.object(self.bot.urllib.request, "urlopen", side_effect=response) as network:
                    self.bot.main()
                    self.assertEqual(network.call_count, 1)
                self.assertEqual(store.read()["next_offset"], 5)
                self.assertIn("-100:9", store.read()["messages"])
                self.assertEqual(self.cli.call_count, 1)  # Resync never submits business.
                with patch.object(self.bot.sys, "argv", ["bot", "--once"]), \
                        patch.object(self.bot.urllib.request, "urlopen", side_effect=[
                            self.response({"ok": True, "result": pending}),
                            self.response({"ok": True, "result": {"message_id": 20}}),
                            self.response({"ok": True, "result": {"message_id": 21}})]) as network:
                    self.bot.main()
                    params = self.bot.urllib.parse.parse_qs(network.call_args_list[0].args[0].data.decode())
                    self.assertEqual(params["offset"], ["5"])
                state = store.read()
                self.assertEqual(self.cli.call_count, 2)
                self.assertEqual(state["next_offset"], 7)
                self.assertEqual(state["last_observed_at"], 519401)
                self.assertIn("900", state["receipts"])
                self.assertEqual(state["receipts"]["1:5"], "duplicate_message")
                self.assertEqual(state["receipts"]["1:6"], "submitted")

    def test_unexpected_lower_id_blocks_without_replaying_or_lowering_offset(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.bot.UpdateStore(Path(folder), "fixture", "bot")
            with patch.object(self.bot.time, "time", return_value=1000):
                self.bot.process_updates(self.tg, [{"update_id": 900, "message": self.msg(message_id=1)}], {"-100"}, store)
                with self.assertRaisesRegex(ValueError, "resync"):
                    self.bot.process_updates(self.tg, [{"update_id": 5, "message": self.msg(message_id=2)}], {"-100"}, store)
            state = store.read()
            self.assertEqual(state["next_offset"], 901)
            self.assertNotIn("5", state["receipts"])
            self.assertTrue(state["resync_required"])
            self.cli.assert_called_once()

    def test_malformed_success_and_json_429_share_cooldown_instead_of_busy_loop(self):
        cases = [("get", {"ok": True}), ("get", {"ok": True, "result": {}}),
                 ("send", {"ok": True, "result": {}}),
                 ("send", {"ok": True, "result": {"message_id": True}}),
                 ("get", {"ok": False, "error_code": 429, "parameters": {"retry_after": 17}})]
        for operation, payload in cases:
            with self.subTest(operation=operation, payload=payload):
                with patch.object(self.bot.time, "time", return_value=1000), \
                        patch.object(self.bot.urllib.request, "urlopen", return_value=self.response(payload)) as network:
                    client = self.bot.Telegram("123:FAKE_SECRET")
                    if operation == "send":
                        with self.assertRaises(RuntimeError):
                            client.send(11, "fixture")
                    else:
                        self.assertIsNone(client.get_updates())
                    self.assertGreater(client.retry_after, 0)
                    self.assertIsNone(client.get_updates())
                    self.assertEqual(network.call_count, 1)

    def test_poll_guard_is_conservative_before_week_and_empty_polls_do_not_reset_it(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.bot.UpdateStore(Path(folder), "fixture", "bot")
            with patch.object(self.bot.time, "time", return_value=1000):
                self.bot.process_updates(self.tg, [{"update_id": 900, "message": self.msg(message_id=1)}], {"-100"}, store)
            with patch.object(self.bot.time, "time", return_value=605799):
                self.bot.process_updates(self.tg, [], {"-100"}, store)
                with self.assertRaisesRegex(ValueError, "resync"):
                    store.prepare_poll()  # Do not start a 30+15s request one second before a week.
            self.assertEqual(store.read()["last_observed_at"], 0)
            self.assertLess(store.read()["idle_resync_seconds"], 604800)

    def test_empty_or_invalid_manual_resync_stays_blocked_and_active_resync_is_denied(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.bot.UpdateStore(Path(folder), "fixture", "bot")
            with patch.object(self.bot.time, "time", return_value=1000):
                self.bot.process_updates(self.tg, [{"update_id": 900, "message": self.msg(message_id=1)}], {"-100"}, store)
                with self.assertRaises(ValueError):
                    self.bot.resynchronize(self.tg, store)
                self.tg.get_updates.assert_not_called()
            with patch.object(self.bot.time, "time", return_value=605801):
                for updates in ([], None, {}, [{"update_id": True}], [{"update_id": -1}]):
                    with self.subTest(updates=updates):
                        self.tg.get_updates.return_value = updates
                        with self.assertRaisesRegex(ValueError, "resync"):
                            self.bot.resynchronize(self.tg, store)
                        self.assertEqual(store.read()["next_offset"], 901)
                        self.assertTrue(store.read()["resync_required"])
                        self.assertIn("-100:1", store.read()["messages"])
            self.cli.assert_called_once()

    def test_durable_receipt_blocks_replay_after_reply_error(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.bot.UpdateStore(Path(folder), "fixture-account", "fixture-bot")
            update = {"update_id": 51, "message": self.msg()}
            self.tg.send.side_effect = [None, RuntimeError("reply failed"), None, None]
            self.bot.process_updates(self.tg, [update], {"-100"}, store)
            again = self.bot.UpdateStore(Path(folder), "fixture-account", "fixture-bot")
            self.bot.process_updates(self.tg, [update], {"-100"}, again)
            self.cli.assert_called_once()
            self.assertEqual(again.read()["next_offset"], 52)

    def test_account_scopes_do_not_share_receipts(self):
        with tempfile.TemporaryDirectory() as folder:
            a = self.bot.UpdateStore(Path(folder), "A", "bot")
            b = self.bot.UpdateStore(Path(folder), "B", "bot")
            update = {"update_id": 1, "message": self.msg()}
            self.bot.process_updates(self.tg, [update, update], {"-100"}, a)
            self.bot.process_updates(self.tg, [update], {"-100"}, b)
            self.assertEqual(self.cli.call_count, 2)

    def test_cli_failure_receipt_is_unknown_not_success(self):
        self.cli.return_value = (None, "timeout")
        with tempfile.TemporaryDirectory() as folder:
            store = self.bot.UpdateStore(Path(folder), "fixture", "bot")
            self.bot.process_updates(self.tg, [{"update_id": 1, "message": self.msg()}], {"-100"}, store)
            self.assertEqual(store.read()["receipts"]["1"], "submission_unknown")

    def test_pikpak_cli_failures_stop_claiming_and_persist_account_gate_across_restart(self):
        cases = (
            ("nonzero", subprocess.CompletedProcess([], 7, "", "NEVER_LOG")),
            ("timeout", subprocess.TimeoutExpired(["pikpak"], 180)),
            ("unknown", subprocess.CompletedProcess([], 0, "{}", "")),
        )
        for label, failure in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as folder:
                other = module("pikpak-telegram-bot", "tg_bot")
                tg = Mock()
                tg.send.return_value = {"message_id": 1}
                store = other.UpdateStore(Path(folder), "fixture-account", "fixture-bot")
                updates = [{"update_id": n, "message": self.msg(message_id=n)} for n in (1, 2)]

                def cli(argv, **kwargs):
                    self.assertEqual(argv[1:3], ["task", "add"])
                    if isinstance(failure, subprocess.TimeoutExpired):
                        raise failure
                    return failure

                with patch.object(other.time, "time", return_value=1000), \
                        patch.object(other.subprocess, "run", side_effect=cli) as run:
                    self.assertEqual(other.process_updates(tg, updates, {"-100"}, store), 2)
                state = store.read()
                self.assertEqual(state["receipts"], {"1": "submission_unknown"})
                self.assertEqual(state["next_offset"], 2)
                self.assertGreater(store.pikpak_retry_after(now=1000), 0)
                self.assertEqual(run.call_count, 1)
                self.assertTrue(all("rm" not in call.args[0] for call in run.call_args_list))

                restarted = module("pikpak-telegram-bot", "tg_bot")
                restarted_store = restarted.UpdateStore(Path(folder), "fixture-account", "other-bot")
                with patch.object(restarted.time, "time", return_value=1001), \
                        patch.object(restarted.subprocess, "run",
                                     side_effect=AssertionError("persistent account cooldown must gate restart")) as blocked:
                    restarted.process_updates(tg, [{"update_id": 2, "message": self.msg(message_id=2)}],
                                               {"-100"}, restarted_store)
                blocked.assert_not_called()
                self.assertNotIn("2", restarted_store.read()["receipts"])

    def test_pikpak_cooldown_ledger_is_shared_between_telegram_and_guardian(self):
        guardian = module("pikpak-task-guardian", "task_guardian")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            account = "shared-fixture-account"
            bot_store = self.bot.UpdateStore(root, account, "fixture-bot")
            tg = Mock()
            tg.send.return_value = {"message_id": 1}
            failure = self.bot.PikPakFailure("rate_limit", retry_after=120)
            with patch.object(self.bot, "pikpak_json", return_value=(None, failure)), \
                    patch.object(self.bot.time, "time", return_value=1000):
                self.bot.process_updates(tg, [{"update_id": 1, "message": self.msg()}], {"-100"}, bot_store)

            guardian_state = root / "guardian.json"
            with patch.object(guardian, "fetch_tasks", side_effect=AssertionError("shared PikPak cooldown must gate guardian")):
                state = guardian.once(state_path=guardian_state, account_scope=account, now=1001)
            self.assertEqual(state["run_status"], "degraded")

            other_root = root / "reverse"
            other_root.mkdir()
            guardian_state = other_root / "guardian.json"
            with patch.object(guardian, "fetch_tasks", return_value=(None, {"kind": "rate_limit", "retry_after": 120})):
                failed = guardian.once(state_path=guardian_state, account_scope=account, now=2000)
            self.assertEqual(failed["run_status"], "degraded")
            other_bot = module("pikpak-telegram-bot", "tg_bot")
            other_store = other_bot.UpdateStore(other_root, account, "fixture-bot")
            self.assertGreater(other_store.pikpak_retry_after(now=2001), 0)

    def test_bot_cooldown_starts_at_observed_cli_failure_return(self):
        with tempfile.TemporaryDirectory() as folder:
            clock = [1000]
            store = self.bot.UpdateStore(Path(folder), "fixture-account", "fixture-bot")
            tg = Mock()
            tg.send.return_value = {"message_id": 1}
            failure = self.bot.PikPakFailure("rate_limit", retry_after=120)
            def cli(*args, **kwargs):
                clock[0] = 1180
                return None, failure
            with patch.object(self.bot, "pikpak_json", side_effect=cli), \
                    patch.object(self.bot.time, "time", side_effect=lambda: clock[0]):
                self.bot.process_updates(tg, [{"update_id": 1, "message": self.msg()}], {"-100"}, store)
            self.assertGreaterEqual(store.pikpak_retry_after(now=1180), 120)
            self.assertGreater(store.pikpak_retry_after(now=1181), 0)

    def test_bot_main_once_cli_degradation_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as folder:
            other = module("pikpak-telegram-bot", "tg_bot")
            updates = [{"update_id": 1, "message": self.msg(message_id=1)}]

            def cli(argv, **kwargs):
                if argv[1:] == ["version"]:
                    return subprocess.CompletedProcess(argv, 0, "fixture-version", "")
                self.assertEqual(argv[1:3], ["task", "add"])
                return subprocess.CompletedProcess(argv, 7, "", "NEVER_LOG")

            with patch.dict(os.environ, {"PIKPAK_AUTOMATION_STATE_DIR": folder,
                                         "PIKPAK_ALLOWED_CHAT_IDS": "-100"}), \
                    patch.object(other.sys, "argv", ["bot", "--once"]), \
                    patch.object(other.time, "time", return_value=1000), \
                    patch.object(other.subprocess, "run", side_effect=cli) as run, \
                    patch.object(other.urllib.request, "urlopen", side_effect=[
                        self.response({"ok": True, "result": updates}),
                        self.response({"ok": True, "result": {"message_id": 1}}),
                        self.response({"ok": True, "result": {"message_id": 2}})]) as network:
                with self.assertRaises(SystemExit) as stopped:
                    other.main()
            self.assertNotEqual(stopped.exception.code, 0)
            self.assertEqual(run.call_count, 2)  # version + one add; no replay
            self.assertEqual(network.call_count, 3)  # poll + two replies only

    def test_distinct_updates_for_same_message_submit_only_once(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.bot.UpdateStore(Path(folder), "fixture", "bot")
            message = self.msg(message_id=9)
            self.bot.process_updates(self.tg, [{"update_id": 1, "message": message}, {"update_id": 2, "message": message}], {"-100"}, store)
            self.cli.assert_called_once()
            self.assertEqual(store.read()["next_offset"], 3)

    def test_claim_write_failure_prevents_submission(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.bot.UpdateStore(Path(folder), "fixture", "bot")
            with patch.object(store, "save", side_effect=OSError("disk failure")):
                with self.assertRaises(OSError):
                    self.bot.process_updates(self.tg, [{"update_id": 1, "message": self.msg()}], {"-100"}, store)
            self.cli.assert_not_called()

    def test_parallel_handlers_share_one_claim(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.bot.UpdateStore(Path(folder), "fixture", "bot")
            update = {"update_id": 1, "message": self.msg(message_id=1)}
            errors = []
            def run():
                try:
                    self.bot.process_updates(self.tg, [update], {"-100"}, store)
                except Exception as error:
                    errors.append(type(error).__name__)
            workers = [threading.Thread(target=run) for _ in range(2)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=5)
                self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])
            self.cli.assert_called_once()

    def test_send_failure_is_not_reported_delivered(self):
        client = self.bot.Telegram("123:FAKE_SECRET")
        with patch.object(client, "_call", return_value=None):
            with self.assertRaises(RuntimeError):
                client.send(11, "fixture")

    def test_folder_lookup_paginates_and_refuses_duplicate_names(self):
        self.cli.side_effect = [({"files": [], "next_page_token": "next"}, None),
                                ({"files": [{"id": "F", "name": "target", "kind": "drive#folder"}]}, None)]
        self.assertEqual(self.bot.find_folder_id("target"), ("F", None))
        self.cli.side_effect = [({"files": [{"id": "F", "name": "target", "kind": "drive#folder"},
                                             {"id": "G", "name": "target", "kind": "drive#folder"}]}, None)]
        fid, error = self.bot.find_folder_id("target")
        self.assertIsNone(fid)
        self.assertIsNotNone(error)

    def test_folder_lookup_unknown_schema_never_reaches_task_add_through_wrapper(self):
        invalid = [
            {"files": [{"id": "F1", "name": "target"}], "next_page_token": []},
            {"files": [{"id": "F1", "name": "target"}], "next_page_token": False},
            {"files": [{"id": [], "name": "target"}]},
            {"files": [{"id": "F1", "name": 42}]},
            {"files": [{"id": "F1", "name": "target", "kind": []}]},
        ]
        for payload in invalid:
            with self.subTest(payload=payload):
                other = module("pikpak-telegram-bot", "tg_bot")
                tg = Mock()

                def cli(argv, **kwargs):
                    self.assertEqual(argv[1:4], ["ls", "--type", "folder"])
                    return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

                with patch.object(other.subprocess, "run", side_effect=cli) as run:
                    other.handle_save(tg, 11, "/save https://example.test/file to target")
                run.assert_called_once()
                self.assertNotIn(["task", "add"], [call.args[0][1:3] for call in run.call_args_list])

    def test_folder_lookup_requires_drive_folder_kind(self):
        for kind in ("drive#file", "drive#unknown", None):
            with self.subTest(kind=kind):
                other = module("pikpak-telegram-bot", "tg_bot")
                payload = {"files": [{"id": "F1", "name": "target"}]}
                if kind is not None:
                    payload["files"][0]["kind"] = kind
                with patch.object(other, "pikpak_json", return_value=(payload, None)):
                    folder_id, error = other.find_folder_id("target")
                self.assertIsNone(folder_id)
                self.assertIsNotNone(error)

    def test_ls_unknown_schema_is_not_empty_through_actual_cli_wrapper(self):
        other = module("pikpak-telegram-bot", "tg_bot")
        invalid = [{}, {"files": None}, {"files": {}}, {"files": [None]},
                   {"files": [[]]}, {"files": [{"name": 42}]},
                   {"files": [{"name": "fixture", "kind": []}]},
                   *({"files": [], "next_page_token": token} for token in (False, 0, [], {}, None))]
        for payload in invalid:
            with self.subTest(payload=payload):
                tg = Mock()
                proc = subprocess.CompletedProcess([], 0, json.dumps(payload), "NEVER_LOG")
                with patch.object(other.subprocess, "run", return_value=proc) as run:
                    other.handle_ls(tg, 11)
                text = "\n".join(call.args[1] for call in tg.send.call_args_list)
                self.assertNotIn("是空的", text)
                self.assertIn("未确认", text)
                run.assert_called_once()
                self.assertEqual(run.call_args.args[0][1:], ["ls", "-l", "10", "-F", "json"])

    def test_ls_only_explicit_empty_final_page_is_empty(self):
        other = module("pikpak-telegram-bot", "tg_bot")
        for payload, empty in (({"files": []}, True), ({"files": [], "next_page_token": ""}, True),
                               ({"files": [], "next_page_token": "page2"}, False),
                               ({"files": [{"name": "fixture"}], "next_page_token": "page2"}, False)):
            with self.subTest(payload=payload):
                tg = Mock()
                with patch.object(other.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(payload), "")):
                    other.handle_ls(tg, 11)
                text = "\n".join(call.args[1] for call in tg.send.call_args_list)
                self.assertEqual("根目录是空的。" in text, empty)
                if payload.get("next_page_token"):
                    self.assertIn("后页", text)

    def test_ls_notification_keeps_credential_like_scalar_names_safe(self):
        other = module("pikpak-telegram-bot", "tg_bot")
        secret = "SYNTHETIC_LS_SECRET"
        payload = {"files": [{"name": "access_token=" + secret, "kind": "drive#file"}]}
        tg = Mock()
        proc = subprocess.CompletedProcess([], 0, json.dumps(payload), "")
        with patch.object(other.subprocess, "run", return_value=proc):
            other.handle_ls(tg, 11)
        text = "\n".join(call.args[1] for call in tg.send.call_args_list)
        self.assertNotIn(secret, text)
        self.assertIn("unknown", text)

    def test_corrupt_receipts_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.bot.UpdateStore(Path(folder), "A", "bot")
            store.path.write_text("{broken")
            with self.assertRaises(ValueError):
                self.bot.process_updates(self.tg, [{"update_id": 1, "message": self.msg()}], {"-100"}, store)
            self.cli.assert_not_called()

    def test_receipt_read_rejects_future_epoch_and_unreferenced_duplicates(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.bot.UpdateStore(Path(folder), "fixture", "bot")
            base = {"scope": store.scope, "receipts": {"1": "started"},
                    "messages": {}, "next_offset": 2, "epoch": 1,
                    "last_observed_at": 1000, "idle_resync_seconds": self.bot.IDLE_RESYNC_SECONDS,
                    "resync_required": False}
            for mutation in (
                    {"receipts": {"1": "started", "2:1": "started"}},
                    {"receipts": {"1": "duplicate_message", "2": "started"},
                     "messages": {"-100:7": "2"}, "next_offset": 3, "epoch": 0}):
                with self.subTest(mutation=mutation):
                    candidate = dict(base)
                    candidate.update(mutation)
                    store.path.write_text(json.dumps(candidate), encoding="utf-8")
                    store.path.chmod(0o600)
                    with self.assertRaises(ValueError):
                        store.read()

    def test_process_updates_never_writes_message_key_with_missing_chat_or_message_id(self):
        for missing in ("chat", "message_id"):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as folder:
                store = self.bot.UpdateStore(Path(folder), "fixture", "bot")
                message = self.msg(message_id=1)
                if missing == "chat":
                    message.pop("chat")
                else:
                    message["message_id"] = None
                with patch.object(self.bot.time, "time", return_value=1000):
                    self.bot.process_updates(self.tg, [{"update_id": 1, "message": message}], {"-100"}, store)
                self.assertEqual(store.read()["messages"], {})
                self.assertFalse(any("None" in key for key in store.read()["messages"]))

    def test_update_ledger_deep_schema_rejects_corruption_without_changing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.bot.UpdateStore(Path(folder), "A", "bot")
            valid = {"scope": store.scope, "receipts": {"1": "started", "1:5": "uncertain"},
                     "messages": {"-100:7": "1:5"}, "next_offset": 8, "epoch": 1,
                     "last_observed_at": 1000, "idle_resync_seconds": self.bot.IDLE_RESYNC_SECONDS,
                     "resync_required": False}
            store.path.write_text(json.dumps(valid), encoding="utf-8")
            store.path.chmod(0o600)
            self.assertEqual(store.read()["receipts"]["1"], "started")
            mutations = (
                {"receipts": {"1": None, "1:5": "uncertain"}},
                {"receipts": {"1": [], "1:5": "uncertain"}},
                {"receipts": {"1": "not-a-receipt", "1:5": "uncertain"}},
                {"receipts": {"1.0": "started", "1:5": "uncertain"}},
                {"messages": {"-100:7": None}},
                {"messages": {"-100:7": "99"}},
                {"messages": {"bad-key": "1:5"}},
                {"receipts": {"1": "duplicate_message", "1:5": "uncertain"}, "messages": {}},
            )
            for mutation in mutations:
                with self.subTest(mutation=mutation):
                    candidate = dict(valid)
                    candidate.update(mutation)
                    store.path.write_text(json.dumps(candidate), encoding="utf-8")
                    store.path.chmod(0o600)
                    before = store.path.read_bytes()
                    with self.assertRaises(ValueError):
                        store.read()
                    self.assertEqual(store.path.read_bytes(), before)

    def test_duplicate_message_references_must_be_canonical_and_live(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.bot.UpdateStore(Path(folder), "A", "bot")
            base = {"scope": store.scope, "receipts": {"1": "started", "2": "duplicate_message"},
                    "messages": {"-100:7": "1"}, "duplicate_messages": {"2": "-100:7"},
                    "next_offset": 3, "epoch": 0, "last_observed_at": 1000,
                    "idle_resync_seconds": self.bot.IDLE_RESYNC_SECONDS, "resync_required": False}
            store.path.write_text(json.dumps(base), encoding="utf-8")
            store.path.chmod(0o600)
            self.assertEqual(store.read()["duplicate_messages"]["2"], "-100:7")
            for mutation in (
                    {"duplicate_messages": {"2": "-100:99"}},
                    {"messages": {"-100:7": "2"}, "duplicate_messages": {}},
                    {"messages": {"-100:7": "1"}, "duplicate_messages": {"2": "-100:99"}}):
                with self.subTest(mutation=mutation):
                    candidate = dict(base)
                    candidate.update(mutation)
                    store.path.write_text(json.dumps(candidate), encoding="utf-8")
                    store.path.chmod(0o600)
                    with self.assertRaises(ValueError):
                        store.read()


class GuardianTests(unittest.TestCase):
    def setUp(self):
        self.guard = module("pikpak-task-guardian", "task_guardian")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state.json"
        self.notes = patch.object(self.guard, "notify").start()
        self.cli = patch.object(self.guard, "pikpak_json", return_value=({"task_id": "NEXT"}, None)).start()
        self.direct_run = patch.object(self.guard.subprocess, "run", side_effect=AssertionError("unmocked process forbidden")).start()
        self.addCleanup(patch.stopall)

    def once(self, tasks, **kwargs):
        return self.guard.once({"tasks": tasks}, state_path=self.path,
                               account_scope="fixture-account", **kwargs)

    def test_phase_enum_is_counted_and_notification_is_opt_in(self):
        state = self.once([{"id": "T1", "phase": "PHASE_TYPE_COMPLETE", "name": "fixture"}])
        self.assertIn("T1", state["seen_done"])
        self.notes.assert_not_called()
        self.cli.assert_not_called()

    def test_same_completed_task_not_notified_twice(self):
        tasks = [{"id": "T1", "status": "Complete"}]
        self.notes.return_value = True
        with patch.object(self.guard, "fetch_tasks", return_value=(tasks, None)):
            for _ in range(2):
                self.guard.once(state_path=self.path, account_scope="fixture-account", enable_notifications=True)
        self.notes.assert_called_once()

    def test_notification_uses_static_applescript_and_argv(self):
        other = module("pikpak-task-guardian", "task_guardian")
        attack = 'name" & do shell script "never execute'
        proc = subprocess.CompletedProcess([], 0)
        with patch.object(other.sys, "platform", "darwin"), patch.object(other.subprocess, "run", return_value=proc) as run:
            self.assertTrue(other.notify("title", attack))
        argv = run.call_args.args[0]
        self.assertNotIn(attack, argv[2])
        self.assertIn(attack, argv)

    def test_guardian_notification_metadata_is_allowlisted_and_bounded(self):
        other = module("pikpak-task-guardian", "task_guardian")
        secret = "SYNTHETIC_GUARDIAN_SECRET"
        unsafe = [None, True, 42, {"access_token": secret}, [secret],
                  "password: " + secret, "https://example.test/?token=" + secret,
                  "x" * 121, "name\n" + secret]
        with patch.object(other.sys, "platform", "darwin"), \
                patch.object(other.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
            for value in unsafe:
                with self.subTest(value=value):
                    run.reset_mock()
                    self.assertTrue(other.notify("PikPak", value))
                    argv = run.call_args.args[0]
                    self.assertEqual(argv[4], "PikPak")
                    self.assertEqual(argv[5], "unknown")
                    self.assertNotIn(secret, argv)

    def test_once_sanitizes_task_name_before_notification(self):
        other = module("pikpak-task-guardian", "task_guardian")
        secret = "SYNTHETIC_GUARDIAN_TASK_SECRET"
        notes = Mock(return_value=True)
        with patch.object(other, "fetch_tasks", return_value=([{
                "id": "T1", "status": "Complete", "name": {"access_token": secret}}], None)), \
                patch.object(other, "notify", side_effect=notes):
            other.once(state_path=self.path, account_scope="fixture-account", enable_notifications=True)
        notes.assert_called_once_with("PikPak 转存完成", "unknown")
        self.assertNotIn(secret, repr(notes.call_args))

    def test_notification_nonzero_is_failure(self):
        other = module("pikpak-task-guardian", "task_guardian")
        with patch.object(other.sys, "platform", "darwin"), patch.object(other.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)):
            self.assertFalse(other.notify("title", "fixture"))

    def test_fixture_mode_disallows_notifications_too(self):
        self.once([{"id": "T1", "status": "Complete"}], enable_notifications=True)
        self.notes.assert_not_called()

    def test_retry_requires_exact_id_not_name_match(self):
        state = {}
        ok = self.guard.retry_task({"id": "T1", "name": "film"},
                [{"url": "https://example.test/f", "note": "film"}], state)
        self.assertFalse(ok)
        self.cli.assert_not_called()
        self.direct_run.assert_not_called()

    def test_fixture_mode_never_retries_even_when_opted_in(self):
        with patch.object(self.guard.subprocess, "run", side_effect=AssertionError("No process")):
            self.once([{"id": "T1", "phase": "PHASE_TYPE_ERROR"}], retry_known=True,
                      sources=[{"task_id": "T1", "url": "https://example.test/f"}])
        self.cli.assert_not_called()

    def test_corrupt_state_does_not_reset_dedup(self):
        self.path.write_text("{broken")
        with self.assertRaises(ValueError):
            self.once([{"id": "T1", "status": "Complete"}], enable_notifications=True)
        self.notes.assert_not_called()

    def test_account_mismatch_fails_closed(self):
        self.once([])
        with self.assertRaises(ValueError):
            self.guard.once({"tasks": []}, state_path=self.path, account_scope="another-account")

    def test_failed_notification_is_not_recorded_as_delivered(self):
        self.notes.return_value = False
        with patch.object(self.guard, "fetch_tasks", return_value=([{"id": "T1", "status": "Complete"}], None)):
            state = self.guard.once(state_path=self.path, account_scope="fixture-account", enable_notifications=True)
        self.assertEqual(state["events"]["complete:T1"], "failed")

    def test_fixture_cli_never_accesses_home_or_subprocess(self):
        fixture = Path(self.temp.name) / "fixture.json"
        fixture.write_text('{"tasks":[{"id":"offline","status":"Error"}]}')
        fixture.chmod(0o600)
        argv = ["guardian", "--once", "--from-fixture", str(fixture), "--state-dir", str(Path(self.temp.name) / "state")]
        with patch.object(self.guard.sys, "argv", argv), patch.object(self.guard.Path, "home", side_effect=AssertionError("home forbidden")):
            self.guard.main()
        self.direct_run.assert_not_called()
        self.cli.assert_not_called()
        self.notes.assert_not_called()

    def test_pagination_loop_is_error_not_partial_success(self):
        self.cli.return_value = ({"tasks": [{"id": "A"}], "next_page_token": "same"}, None)
        tasks, err = self.guard.fetch_tasks(None)
        self.assertIsNone(tasks)
        self.assertIsNotNone(err)

    def test_task_listing_rejects_non_string_next_page_tokens(self):
        other = module("pikpak-task-guardian", "task_guardian")
        for token in ([], False, 0, {}):
            with self.subTest(token=token):
                payload = {"tasks": [{"id": "A"}], "next_page_token": token}
                with patch.object(other.subprocess, "run", return_value=subprocess.CompletedProcess(
                        [], 0, json.dumps(payload), "")) as run:
                    tasks, error = other.fetch_tasks(None)
                self.assertIsNone(tasks)
                self.assertIsNotNone(error)
                self.assertIn("分页", str(error))
                run.assert_called_once()

    def test_unknown_phase_does_not_look_successful(self):
        state = self.once([{"id": "T1", "phase": "PHASE_TYPE_NEW_UNKNOWN"}])
        self.assertEqual(state["counts"]["unknown"], 1)
        self.assertEqual(state["seen_done"], [])

    def test_state_atomic_replace_failure_preserves_previous_file(self):
        self.guard.save_json(self.path, {"value": 1})
        with patch.object(self.guard.os, "replace", side_effect=OSError("fixture failure")):
            with self.assertRaises(OSError):
                self.guard.save_json(self.path, {"value": 2})
        self.assertEqual(json.loads(self.path.read_text()), {"value": 1})
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_first_shared_failure_stops_round_and_persists_account_cooldown(self):
        tasks = [{"id": f"T{i}", "status": "Error"} for i in range(6)]
        sources = [{"task_id": t["id"], "url": f"https://example.test/{t['id']}"} for t in tasks]
        for kind, failure in (
            ("rate_limit", subprocess.CompletedProcess([], 1, '{"error_code":429,"parameters":{"retry_after":120},"secret":"NEVER_LOG"}', 'NEVER_LOG')),
            ("service", subprocess.CompletedProcess([], 1, '{"error_code":503}', 'NEVER_LOG')),
            ("transport", subprocess.TimeoutExpired(["fake"], 180)),
            ("unknown", subprocess.CompletedProcess([], 1, '', 'NEVER_LOG')),
        ):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as folder:
                other = module("pikpak-task-guardian", "task_guardian")
                path = Path(folder) / "state.json"
                listed = subprocess.CompletedProcess([], 0, json.dumps({"tasks": tasks}), "")
                out = io.StringIO()
                with patch.object(other.subprocess, "run", side_effect=[listed, failure, listed, failure, listed, failure, listed, failure]) as run, redirect_stdout(out):
                    state = other.once(state_path=path, account_scope="fixture", retry_known=True, sources=sources, now=1000)
                    self.assertEqual(run.call_count, 2)  # listing + one add, not six adds
                self.assertEqual(state["run_status"], "degraded")
                self.assertGreater(state["account_retry"]["cooldown_until"], 1000)
                self.assertEqual(state["account_retry"]["last_failure"]["kind"], kind)
                self.assertEqual(len(state["jobs"]), 1)
                self.assertEqual(next(iter(state["jobs"].values()))["status"], "unknown")
                self.assertNotIn("NEVER_LOG", out.getvalue() + path.read_text())
                self.assertNotIn("巡检完成", out.getvalue())
                again = module("pikpak-task-guardian", "task_guardian")
                with patch.object(again.subprocess, "run", side_effect=AssertionError("persistent cooldown must gate all account work")) as run:
                    cooled = again.once(state_path=path, account_scope="fixture", retry_known=True, sources=sources, now=1001)
                run.assert_not_called()
                self.assertEqual(cooled["run_status"], "degraded")

    def test_default_clock_slow_task_ls_failure_starts_cooldown_at_return(self):
        other = module("pikpak-task-guardian", "task_guardian")
        clock = [1000]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            def cli(argv, **kwargs):
                clock[0] = 1180
                return subprocess.CompletedProcess(
                    argv, 1, '{"error_code":429,"parameters":{"retry_after":120}}', "")
            with patch.object(other.time, "time", side_effect=lambda: clock[0]), \
                    patch.object(other.subprocess, "run", side_effect=cli) as run:
                state = other.once(state_path=path, account_scope="fixture")
            self.assertEqual(state["run_status"], "degraded")
            self.assertEqual(state["account_retry"]["last_failure"]["kind"], "rate_limit")
            self.assertGreaterEqual(state["account_retry"]["cooldown_until"], 1300)
            self.assertEqual(run.call_count, 1)

    def test_default_clock_slow_task_add_failure_starts_cooldown_at_return(self):
        other = module("pikpak-task-guardian", "task_guardian")
        tasks = [{"id": "T1", "status": "Error"}]
        source = [{"task_id": "T1", "url": "https://example.test/T1"}]
        clock = [1000]
        calls = [0]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            def cli(argv, **kwargs):
                calls[0] += 1
                clock[0] = 1180
                if calls[0] == 1:
                    return subprocess.CompletedProcess(argv, 0, json.dumps({"tasks": tasks}), "")
                return subprocess.CompletedProcess(
                    argv, 1, '{"error_code":429,"parameters":{"retry_after":120}}', "")
            with patch.object(other.time, "time", side_effect=lambda: clock[0]), \
                    patch.object(other.subprocess, "run", side_effect=cli) as run:
                state = other.once(state_path=path, account_scope="fixture", retry_known=True,
                                   sources=source)
            self.assertEqual(state["run_status"], "degraded")
            self.assertEqual(state["account_retry"]["last_failure"]["kind"], "rate_limit")
            self.assertGreaterEqual(state["account_retry"]["cooldown_until"], 1300)
            self.assertEqual(run.call_count, 2)

    def test_default_clock_timeout_starts_cooldown_at_return(self):
        other = module("pikpak-task-guardian", "task_guardian")
        tasks = [{"id": "T1", "status": "Error"}]
        source = [{"task_id": "T1", "url": "https://example.test/T1"}]
        clock = [1000]
        calls = [0]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            def cli(argv, **kwargs):
                calls[0] += 1
                clock[0] = 1180
                if calls[0] == 1:
                    return subprocess.CompletedProcess(argv, 0, json.dumps({"tasks": tasks}), "")
                raise subprocess.TimeoutExpired(argv, 180)
            with patch.object(other.time, "time", side_effect=lambda: clock[0]), \
                    patch.object(other.subprocess, "run", side_effect=cli) as run:
                state = other.once(state_path=path, account_scope="fixture", retry_known=True,
                                   sources=source)
            self.assertEqual(state["run_status"], "degraded")
            self.assertEqual(state["account_retry"]["last_failure"]["kind"], "transport")
            self.assertGreaterEqual(state["account_retry"]["cooldown_until"], 1240)
            self.assertEqual(run.call_count, 2)

    def test_main_default_clock_slow_task_add_uses_observed_return_time(self):
        other = module("pikpak-task-guardian", "task_guardian")
        clock = [1000]
        calls = [0]
        with tempfile.TemporaryDirectory() as folder:
            sources = Path(folder) / "sources.json"
            sources.write_text(json.dumps({"account_scope": "fixture", "sources": [
                {"task_id": "T1", "url": "https://example.test/T1"}]}))
            sources.chmod(0o600)
            def cli(argv, **kwargs):
                calls[0] += 1
                clock[0] = 1180
                if calls[0] == 1:
                    return subprocess.CompletedProcess(
                        argv, 0, '{"tasks":[{"id":"T1","status":"Error"}]}', "")
                return subprocess.CompletedProcess(
                    argv, 1, '{"error_code":429,"parameters":{"retry_after":120}}', "")
            argv = ["guardian", "--once", "--account-scope", "fixture",
                    "--state-dir", folder, "--retry-known", "--sources", str(sources)]
            with patch.object(other.sys, "argv", argv), \
                    patch.object(other.time, "time", side_effect=lambda: clock[0]), \
                    patch.object(other.subprocess, "run", side_effect=cli) as run:
                with self.assertRaises(SystemExit) as stopped:
                    other.main()
            self.assertEqual(stopped.exception.code, 1)
            self.assertEqual(run.call_count, 2)
            state_path = next(Path(folder).glob("guardian-*.json"))
            state = json.loads(state_path.read_text())
            self.assertEqual(state["run_status"], "degraded")
            self.assertGreaterEqual(state["account_retry"]["cooldown_until"], 1300)

    def test_slow_guardian_failures_cool_down_from_observed_return_time(self):
        tasks = [{"id": "T1", "status": "Error"}]
        source = [{"task_id": "T1", "url": "https://example.test/T1"}]
        listed = lambda argv: subprocess.CompletedProcess(argv, 0, json.dumps({"tasks": tasks}), "")
        cases = (
            ("slow_ls_429", [subprocess.CompletedProcess([], 1,
                '{"error_code":429,"parameters":{"retry_after":120}}', "")], "rate_limit", 120),
            ("slow_add_429", [listed, subprocess.CompletedProcess([], 1,
                '{"error_code":429,"parameters":{"retry_after":120}}', "")], "rate_limit", 120),
            ("slow_add_timeout", [listed, subprocess.TimeoutExpired(["pikpak"], 180)], "transport", 0),
        )
        for label, outcomes, kind, retry_after in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as folder:
                other = module("pikpak-task-guardian", "task_guardian")
                path = Path(folder) / "state.json"
                clock = [1000]
                index = [0]

                def cli(argv, **kwargs):
                    result = outcomes[index[0]]
                    index[0] += 1
                    if callable(result):
                        result = result(argv)
                    clock[0] = 1180
                    if isinstance(result, subprocess.TimeoutExpired):
                        raise result
                    return subprocess.CompletedProcess(argv, result.returncode, result.stdout, result.stderr)

                with patch.object(other.subprocess, "run", side_effect=cli) as run:
                    state = other.once(state_path=path, account_scope="fixture", retry_known=True,
                                       sources=source, clock=lambda: clock[0])
                self.assertEqual(state["run_status"], "degraded")
                self.assertEqual(state["account_retry"]["last_failure"]["kind"], kind)
                self.assertEqual(state["account_retry"]["last_failure"]["retry_after"], retry_after)
                expected_delay = retry_after or 60
                self.assertGreaterEqual(state["account_retry"]["cooldown_until"], 1180 + expected_delay)
                self.assertEqual(run.call_count, 1 if label == "slow_ls_429" else 2)

                again = module("pikpak-task-guardian", "task_guardian")
                with patch.object(again.subprocess, "run",
                                  side_effect=AssertionError("observed-time cooldown must gate next once")) as blocked:
                    cooled = again.once(state_path=path, account_scope="fixture", retry_known=True,
                                        sources=source, clock=lambda: 1181)
                blocked.assert_not_called()
                self.assertEqual(cooled["run_status"], "degraded")

    def test_account_round_cap_has_persistent_spacing_across_distinct_sources(self):
        other = module("pikpak-task-guardian", "task_guardian")
        tasks = [{"id": f"T{i}", "status": "Error"} for i in range(6)]
        sources = [{"task_id": t["id"], "url": f"https://example.test/{t['id']}"} for t in tasks]
        def cli(argv, **kwargs):
            payload = {"tasks": tasks} if argv[1:3] == ["task", "ls"] else {"task_id": "NEXT-" + argv[3].rsplit("/", 1)[-1]}
            return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")
        with patch.object(other.subprocess, "run", side_effect=cli) as run:
            state = other.once(state_path=self.path, account_scope="fixture", retry_known=True, sources=sources, now=1000)
            self.assertEqual(run.call_count, 4)  # one listing + fixed cap of three adds
        self.assertEqual(state["round_submissions"], 3)
        self.assertEqual(state["run_status"], "limited")
        with patch.object(other.subprocess, "run", side_effect=AssertionError("cap cooldown survives a new once")) as run:
            other.once(state_path=self.path, account_scope="fixture", retry_known=True, sources=sources, now=1001)
        run.assert_not_called()
        with patch.object(other.subprocess, "run", side_effect=cli) as run:
            state = other.once(state_path=self.path, account_scope="fixture", retry_known=True, sources=sources, now=2000)
            self.assertEqual(run.call_count, 4)
        self.assertEqual(len(state["jobs"]), 6)
        self.assertTrue(all(job["attempts"] == 1 for job in state["jobs"].values()))

    def test_main_degraded_once_is_nonzero_and_daemon_waits_persistent_deadline(self):
        sources = Path(self.temp.name) / "sources.json"
        sources.write_text(json.dumps({"account_scope": "fixture", "sources": [
            {"task_id": "T1", "url": "https://example.test/owned"}]}))
        sources.chmod(0o600)
        argv = ["guardian", "--account-scope", "fixture", "--state-dir", self.temp.name,
                "--retry-known", "--sources", str(sources), "--interval", "1"]
        other = module("pikpak-task-guardian", "task_guardian")
        results = [subprocess.CompletedProcess([], 0, '{"tasks":[{"id":"T1","status":"Error"}]}', ""),
                   subprocess.CompletedProcess([], 1, '{"error_code":429,"parameters":{"retry_after":120}}', "")]
        with patch.object(other.sys, "argv", argv + ["--once"]), \
                patch.object(other.time, "time", return_value=1000), \
                patch.object(other.subprocess, "run", side_effect=results) as run:
            with self.assertRaises(SystemExit) as stopped:
                other.main()
            self.assertEqual(stopped.exception.code, 1)
            self.assertEqual(run.call_count, 2)
        with patch.object(other.sys, "argv", argv), \
                patch.object(other.time, "time", return_value=1001), \
                patch.object(other.subprocess, "run", side_effect=AssertionError("cooldown")) as run, \
                patch.object(other.time, "sleep", side_effect=KeyboardInterrupt) as sleep:
            with self.assertRaises(KeyboardInterrupt):
                other.main()
            run.assert_not_called()
            self.assertGreaterEqual(sleep.call_args.args[0], 119)

    def test_cap_deadline_is_durable_before_third_add_even_if_interrupted(self):
        other = module("pikpak-task-guardian", "task_guardian")
        tasks = [{"id": f"T{i}", "status": "Error"} for i in range(4)]
        sources = [{"task_id": t["id"], "url": f"https://example.test/{t['id']}"} for t in tasks]
        listed = subprocess.CompletedProcess([], 0, json.dumps({"tasks": tasks}), "")
        with patch.object(other.subprocess, "run", side_effect=[listed,
                subprocess.CompletedProcess([], 0, '{"task_id":"NEXT0"}', ""),
                subprocess.CompletedProcess([], 0, '{"task_id":"NEXT1"}', ""), KeyboardInterrupt]):
            with self.assertRaises(KeyboardInterrupt):
                other.once(state_path=self.path, account_scope="fixture", retry_known=True, sources=sources, now=1000)
        state = json.loads(self.path.read_text())
        self.assertEqual(state["round_submissions"], 3)
        self.assertGreater(state["account_retry"]["cooldown_until"], 1000)
        with patch.object(other.subprocess, "run", side_effect=AssertionError("cap journal must gate restart")) as run:
            other.once(state_path=self.path, account_scope="fixture", retry_known=True, sources=sources, now=1001)
        run.assert_not_called()

    def test_nested_retry_state_is_validated_before_any_account_work(self):
        original = self.once([])
        job = {"origin_id": "T1", "current_id": "T1", "attempts": 1, "status": "unknown", "next_retry_at": 1000}
        invalid = [{"jobs": {"x": None}}, {"jobs": {"x": dict(job, status="maybe")}},
                   *({"jobs": {"x": dict(job, attempts=value)}} for value in (True, -1, 4, "1")),
                   *({"jobs": {"x": dict(job, next_retry_at=value)}} for value in (-1, float("nan"), float("inf"), "1000")),
                   {"events": {"complete:T1": "end_to_end_delivered"}},
                   {"account_retry": {"cooldown_until": "later", "failures": 1}},
                   {"account_retry": None}, {"account_retry": []}]
        for mutation in invalid:
            with self.subTest(mutation=mutation):
                self.path.write_text(json.dumps(dict(original, **mutation)))
                with self.assertRaises(ValueError):
                    self.once([])
        self.cli.assert_not_called()
        self.notes.assert_not_called()

    def test_cli_failure_envelope_is_allowlisted_without_inferring_exit_status(self):
        other = module("pikpak-task-guardian", "task_guardian")
        for rc, payload, expected in ((1, {"kind": [], "error_code": 503, "secret": "NEVER_LOG"}, "service"),
                                      (1, {"kind": {}}, "unknown"), (429, None, "unknown")):
            with self.subTest(rc=rc, payload=payload):
                stdout = json.dumps(payload) if payload is not None else "not json NEVER_LOG"
                with patch.object(other.subprocess, "run", return_value=subprocess.CompletedProcess([], rc, stdout, "NEVER_LOG")):
                    result, error = other.pikpak_json("task", "add", "https://example.test/f")
                self.assertIsNone(result)
                self.assertEqual(error, {"kind": expected, "retry_after": 0})

    def test_unknown_job_is_not_replayed_after_cooldown_and_disk_reload(self):
        other = module("pikpak-task-guardian", "task_guardian")
        sources = [{"task_id": "T1", "url": "https://example.test/owned"}]
        listed = subprocess.CompletedProcess([], 0, '{"tasks":[{"id":"T1","status":"Error"}]}', "")
        with patch.object(other.subprocess, "run", side_effect=[listed, subprocess.TimeoutExpired(["fake"], 180)]):
            other.once(state_path=self.path, account_scope="fixture", retry_known=True, sources=sources, now=1000)
        before = json.loads(self.path.read_text())["jobs"]
        again = module("pikpak-task-guardian", "task_guardian")
        with patch.object(again.subprocess, "run", return_value=listed) as run:
            state = again.once(state_path=self.path, account_scope="fixture", retry_known=True, sources=sources, now=100000)
            run.assert_called_once()
            self.assertEqual(run.call_args.args[0][1:3], ["task", "ls"])
        self.assertEqual(state["jobs"], before)
        self.assertEqual(next(iter(state["jobs"].values()))["attempts"], 1)
        self.assertEqual(next(iter(state["jobs"].values()))["status"], "unknown")

    def test_disk_lineage_successors_share_original_three_attempt_budget(self):
        sources = [{"task_id": "T1", "url": "https://example.test/owned"}]
        for tid, next_id, now, expected_adds in (("T1", "T2", 1000, 1), ("T2", "T3", 1001, 0),
                                               ("T2", "T3", 2000, 1), ("T3", "T4", 4000, 1),
                                               ("T4", "T5", 8000, 0)):
            with self.subTest(tid=tid, now=now):
                other = module("pikpak-task-guardian", "task_guardian")
                def cli(argv, **kwargs):
                    data = {"tasks": [{"id": tid, "status": "Error"}]} if argv[1:3] == ["task", "ls"] else {"task_id": next_id}
                    return subprocess.CompletedProcess(argv, 0, json.dumps(data), "")
                with patch.object(other.subprocess, "run", side_effect=cli) as run:
                    state = other.once(state_path=self.path, account_scope="fixture", retry_known=True, sources=sources, now=now)
                self.assertEqual(run.call_count, 1 + expected_adds)
        self.assertEqual(len(state["jobs"]), 1)
        self.assertEqual(next(iter(state["jobs"].values()))["attempts"], 3)

    def test_recreated_task_ids_share_retry_budget_and_backoff(self):
        state = {}
        sources = [{"task_id": "T1", "url": "https://example.test/f"}]
        snapshots = []
        def persist(value):
            snapshots.append(json.loads(json.dumps(value)))
        self.cli.side_effect = [({"task_id": "T2"}, None), ({"task_id": "T3"}, None), ({"task_id": "T4"}, None)]
        self.assertTrue(self.guard.retry_task({"id": "T1"}, sources, state, persist=persist, now=1000))
        self.assertFalse(self.guard.retry_task({"id": "T2"}, sources, state, persist=persist, now=1001))
        self.assertTrue(self.guard.retry_task({"id": "T2"}, sources, state, persist=persist, now=2000))
        self.assertTrue(self.guard.retry_task({"id": "T3"}, sources, state, persist=persist, now=4000))
        self.assertFalse(self.guard.retry_task({"id": "T4"}, sources, state, persist=persist, now=8000))
        self.assertEqual(self.cli.call_count, 3)
        self.assertEqual(next(iter(state["jobs"].values()))["attempts"], 3)
        self.assertEqual(next(iter(snapshots[0]["jobs"].values()))["status"], "submitting")
        self.direct_run.assert_not_called()

    def test_unknown_submission_is_never_replayed(self):
        state = {}
        sources = [{"task_id": "T1", "url": "https://example.test/f"}]
        self.cli.return_value = (None, "timeout")
        self.assertFalse(self.guard.retry_task({"id": "T1"}, sources, state, persist=lambda value: None, now=1000))
        self.assertFalse(self.guard.retry_task({"id": "T1"}, sources, state, persist=lambda value: None, now=100000))
        self.cli.assert_called_once()
        self.assertEqual(next(iter(state["jobs"].values()))["status"], "unknown")

    def test_task_listing_follows_pagination(self):
        self.cli.side_effect = [({"tasks": [{"id": "A"}], "next_page_token": "page2"}, None),
                                ({"tasks": [{"id": "B"}]}, None)]
        tasks, err = self.guard.fetch_tasks(None)
        self.assertIsNone(err)
        self.assertEqual([t["id"] for t in tasks], ["A", "B"])
        self.assertIn("page2", self.cli.call_args_list[1].args)


class ClosureSchedulingTests(unittest.TestCase):
    """Bottom-layer synthetic interleavings, never a real CLI or Telegram call."""
    def message(self, uid=1, text="/save https://example.test/owned"):
        return {"update_id": uid, "message": {"message_id": uid, "date": 1000,
                "chat": {"id": 11, "type": "private"}, "from": {"id": 11}, "text": text}}

    def response(self):
        return io.BytesIO(b'{"ok":true,"result":{"message_id":1}}')

    def test_bot_rechecks_shared_cooldown_after_slow_ack(self):
        bot = module("pikpak-telegram-bot", "tg_bot")
        guard = module("pikpak-task-guardian", "task_guardian")
        with tempfile.TemporaryDirectory() as folder:
            store = bot.UpdateStore(Path(folder), "closure", "fixture-bot")
            shared = guard.PikPakCooldownStore(Path(folder), "closure")
            clock = [1000]
            def ack(*args, **kwargs):
                clock[0] = 1001
                shared.record_failure({"kind": "rate_limit", "retry_after": 120}, clock[0])
                return self.response()
            with patch.object(bot.time, "time", side_effect=lambda: clock[0]), \
                    patch.object(bot.urllib.request, "urlopen", side_effect=ack), \
                    patch.object(bot.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, '{"task_id":"NEW"}', "")) as run:
                bot.process_updates(bot.Telegram("fixture-bot", store=store), [self.message(), self.message(2)], {"11"}, store)
            run.assert_not_called()
            self.assertEqual(set(store.read()["receipts"]), {"1"})
            self.assertGreaterEqual(shared.read()["cooldown_until"], 1121)
            self.assertGreater(shared.read()["failures"], 0)

    def test_bot_success_preserves_failure_recorded_during_cli(self):
        bot = module("pikpak-telegram-bot", "tg_bot")
        guard = module("pikpak-task-guardian", "task_guardian")
        for saturated in (False, True):
            with self.subTest(saturated=saturated), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                store = bot.UpdateStore(root, "closure", "fixture-bot")
                shared = guard.PikPakCooldownStore(root, "closure")
                if saturated:
                    for _ in range(6):
                        shared.record_failure({"kind": "rate_limit", "retry_after": 120}, 0)
                def cli(argv, **kwargs):
                    self.assertEqual(store.read()["receipts"]["1"], "started")
                    shared.record_failure({"kind": "rate_limit", "retry_after": 120}, 10001 if saturated else 1001)
                    return subprocess.CompletedProcess(argv, 0, '{"task_id":"NEW"}', "")
                with patch.object(bot.time, "time", return_value=10000 if saturated else 1000), \
                        patch.object(bot.urllib.request, "urlopen", side_effect=lambda *a, **k: self.response()), \
                        patch.object(bot.subprocess, "run", side_effect=cli) as run:
                    bot.process_updates(bot.Telegram("fixture-bot", store=store), [self.message()], {"11"}, store)
                self.assertEqual(run.call_count, 1)
                self.assertEqual(store.read()["receipts"]["1"], "submitted")
                self.assertGreater(shared.read()["failures"], 0)
                self.assertEqual(shared.read()["last_failure"]["kind"], "rate_limit")
                self.assertGreaterEqual(shared.read()["cooldown_until"], 1121)


    def test_guardian_rechecks_shared_cooldown_after_slow_listing(self):
        guard = module("pikpak-task-guardian", "task_guardian")
        bot = module("pikpak-telegram-bot", "tg_bot")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shared = bot.PikPakStore(root, "closure")
            def cli(argv, **kwargs):
                if argv[1:3] == ["task", "ls"]:
                    shared.record_failure(bot.PikPakFailure("rate_limit", 120), now=1001)
                    return subprocess.CompletedProcess(argv, 0, '{"tasks":[{"id":"T1","status":"Error"}]}', "")
                return subprocess.CompletedProcess(argv, 0, '{"task_id":"NEW"}', "")
            with patch.object(guard.subprocess, "run", side_effect=cli) as run:
                state = guard.once(state_path=root / "guardian.json", account_scope="closure", now=1001,
                                   retry_known=True, sources=[{"task_id": "T1", "url": "https://example.test/T1"}])
            self.assertEqual(run.call_count, 1)
            self.assertEqual(state["run_status"], "degraded")
            self.assertEqual(state["jobs"], {})
            self.assertEqual(state["account_retry"]["failures"], shared.read()["failures"])

    def test_guardian_rechecks_cooldown_between_two_adds(self):
        guard = module("pikpak-task-guardian", "task_guardian")
        bot = module("pikpak-telegram-bot", "tg_bot")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shared = bot.PikPakStore(root, "closure")
            tasks = [{"id": "T1", "status": "Error"}, {"id": "T2", "status": "Error"}]
            sources = [{"task_id": t["id"], "url": "https://example.test/" + t["id"]} for t in tasks]
            save = guard.save_json
            injected = [False]
            def persist(path, state):
                save(path, state)
                if "jobs" in state and any(j["status"] == "submitted" for j in state["jobs"].values()) and not injected[0]:
                    injected[0] = True
                    shared.record_failure(bot.PikPakFailure("rate_limit", 120), now=1001)
            def cli(argv, **kwargs):
                payload = {"tasks": tasks} if argv[1:3] == ["task", "ls"] else {"task_id": "NEW"}
                return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")
            with patch.object(guard, "save_json", side_effect=persist), patch.object(guard.subprocess, "run", side_effect=cli) as run:
                state = guard.once(state_path=root / "guardian.json", account_scope="closure", now=1001,
                                   retry_known=True, sources=sources)
            self.assertEqual(run.call_count, 2)
            self.assertEqual(state["run_status"], "degraded")
            self.assertEqual(len(state["jobs"]), 1)
            self.assertGreater(shared.read()["failures"], 0)

    def test_guardian_success_preserves_failure_recorded_during_cli(self):
        guard = module("pikpak-task-guardian", "task_guardian")
        bot = module("pikpak-telegram-bot", "tg_bot")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "guardian.json"
            shared = bot.PikPakStore(root, "closure")
            def cli(argv, **kwargs):
                if argv[1:3] == ["task", "ls"]:
                    return subprocess.CompletedProcess(argv, 0, '{"tasks":[{"id":"T1","status":"Error"}]}', "")
                job = next(iter(json.loads(path.read_text())["jobs"].values()))
                self.assertEqual((job["status"], job["attempts"]), ("submitting", 1))
                shared.record_failure(bot.PikPakFailure("rate_limit", 120), now=1001)
                return subprocess.CompletedProcess(argv, 0, '{"task_id":"NEW"}', "")
            with patch.object(guard.subprocess, "run", side_effect=cli) as run:
                state = guard.once(state_path=path, account_scope="closure", now=1001, retry_known=True,
                                   sources=[{"task_id": "T1", "url": "https://example.test/T1"}])
            self.assertEqual(run.call_count, 2)
            self.assertEqual(next(iter(state["jobs"].values()))["status"], "submitted")
            self.assertEqual(state["run_status"], "degraded")
            self.assertGreater(shared.read()["failures"], 0)
            self.assertEqual(state["account_retry"]["failures"], shared.read()["failures"])
            self.assertGreaterEqual(shared.read()["cooldown_until"], 1121)


    def submission_id_cases(self):
        invalid = [
            {"task_id": "A", "task": {"id": "B"}},
            {"task_id": "A", "task": {"task_id": "B"}},
            {"task": {"id": "A", "task_id": "B"}},
            *({"task_id": "A", "task": value} for value in (None, [], False, "A")),
            *({"task_id": "A", "task": {"id": value}} for value in (None, [], {}, False, 42, "")),
            {"task_id": None, "task": {"id": "A"}},
        ]
        valid = [{"task_id": "A"}, {"task": {"id": "A"}}, {"task": {"task_id": "A"}},
                 {"task_id": "A", "task": {"id": "A", "task_id": "A"}}]
        return [(payload, False) for payload in invalid] + [(payload, True) for payload in valid]

    def test_bot_submission_identity_requires_all_present_ids_to_agree(self):
        for payload, accepted in self.submission_id_cases():
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as folder:
                bot = module("pikpak-telegram-bot", "tg_bot")
                store = bot.UpdateStore(Path(folder), "closure", "fixture-bot")
                with patch.object(bot.time, "time", return_value=1000), \
                        patch.object(bot.urllib.request, "urlopen", side_effect=lambda *a, **k: self.response()), \
                        patch.object(bot.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(payload), "")) as run:
                    tg = bot.Telegram("fixture-bot", store=store)
                    bot.process_updates(tg, [self.message()], {"11"}, store)
                    bot.process_updates(tg, [self.message()], {"11"}, store)
                self.assertEqual(run.call_count, 1)
                self.assertEqual(store.read()["receipts"]["1"], "submitted" if accepted else "submission_unknown")
                self.assertEqual(store.pikpak.read()["failures"] > 0, not accepted)

    def test_guardian_submission_identity_requires_all_present_ids_to_agree(self):
        for payload, accepted in self.submission_id_cases():
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as folder:
                guard = module("pikpak-task-guardian", "task_guardian")
                root = Path(folder)
                def cli(argv, **kwargs):
                    result = {"tasks": [{"id": "T1", "status": "Error"}]} if argv[1:3] == ["task", "ls"] else payload
                    return subprocess.CompletedProcess(argv, 0, json.dumps(result), "")
                with patch.object(guard.subprocess, "run", side_effect=cli) as run:
                    state = guard.once(state_path=root / "guardian.json", account_scope="closure", now=1000,
                                       retry_known=True, sources=[{"task_id": "T1", "url": "https://example.test/T1"}])
                self.assertEqual(run.call_count, 2)
                job = next(iter(state["jobs"].values()))
                self.assertEqual(job["status"], "submitted" if accepted else "unknown")
                self.assertEqual(job["current_id"], "A" if accepted else "T1")
                self.assertEqual(state["run_status"], "ok" if accepted else "degraded")
                self.assertEqual(state["account_retry"]["failures"] > 0, not accepted)


    def test_guardian_late_gate_during_intent_fsync_blocks_actual_add(self):
        guard = module("pikpak-task-guardian", "task_guardian")
        bot = module("pikpak-telegram-bot", "tg_bot")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "guardian.json"
            shared = bot.PikPakStore(root, "closure")
            original = guard.save_json
            def persist(target, state):
                original(target, state)
                if "jobs" in state and any(j["status"] == "submitting" for j in state["jobs"].values()):
                    shared.record_failure(bot.PikPakFailure("rate_limit", 120), now=1001)
            with patch.object(guard, "save_json", side_effect=persist), \
                    patch.object(guard.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, '{"tasks":[{"id":"T1","status":"Error"}]}', "")) as run:
                state = guard.once(state_path=path, account_scope="closure", now=1001, retry_known=True,
                                   sources=[{"task_id": "T1", "url": "https://example.test/T1"}])
            self.assertEqual(run.call_count, 1)
            self.assertEqual(state["run_status"], "degraded")
            self.assertEqual(next(iter(state["jobs"].values()))["status"], "unknown")

    def test_shared_admission_and_success_merge_are_backward_compatible(self):
        bot = module("pikpak-telegram-bot", "tg_bot")
        guard = module("pikpak-task-guardian", "task_guardian")
        for caller in ("bot", "guardian"):
            with self.subTest(caller=caller), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                bstore, gstore = bot.PikPakStore(root, "closure"), guard.PikPakCooldownStore(root, "closure")
                bstore.save({"scope": bstore.scope, "cooldown_until": 1920, "failures": 6,
                             "last_failure": {"kind": "rate_limit", "retry_after": 120}})
                with patch.object(bot.time, "time", return_value=10000):
                    if caller == "bot":
                        started = bstore.admit()
                        writer = gstore
                    else:
                        started, allowed = gstore.admit(now=10000)
                        self.assertTrue(allowed)
                        writer = bstore
                    # Saturation and a delayed observation can leave every old
                    # schema field unchanged; generation must still detect it.
                    if caller == "bot":
                        writer.record_failure({"kind": "rate_limit", "retry_after": 120}, 0)
                    else:
                        writer.record_failure(bot.PikPakFailure("rate_limit", 120), now=0)
                    merged = (bstore if caller == "bot" else gstore).record_success(started)
                self.assertEqual(merged["failures"], 6)
                self.assertEqual(merged["cooldown_until"], 1920)
                self.assertGreater(merged["failure_generation"], 0)

    def test_bot_rechecks_after_slow_folder_lookup(self):
        bot = module("pikpak-telegram-bot", "tg_bot")
        guard = module("pikpak-task-guardian", "task_guardian")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = bot.UpdateStore(root, "closure", "fixture-bot")
            shared = guard.PikPakCooldownStore(root, "closure")
            def cli(argv, **kwargs):
                if argv[1] != "ls":
                    self.fail("new shared cooldown must gate task add after directory lookup")
                shared.record_failure({"kind": "rate_limit", "retry_after": 120}, 1001)
                return subprocess.CompletedProcess(argv, 0, '{"files":[{"id":"F","name":"owned","kind":"drive#folder"}]}', "")
            with patch.object(bot.time, "time", return_value=1001), \
                    patch.object(bot.urllib.request, "urlopen", side_effect=lambda *a, **k: self.response()), \
                    patch.object(bot.subprocess, "run", side_effect=cli) as run:
                bot.process_updates(bot.Telegram("fixture-bot", store=store),
                                    [self.message(text="/save https://example.test/owned to owned")], {"11"}, store)
            self.assertEqual(run.call_count, 1)
            self.assertNotEqual(store.read()["receipts"]["1"], "submitted")


if __name__ == "__main__":
    with safe_offline_environment():
        unittest.main(verbosity=2)
