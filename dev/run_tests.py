#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归测试：用中性 fixture 验证两个脚本的核心行为。

用法：python3 dev/run_tests.py
不访问网络，不依赖 PikPak 账号。
"""

import importlib.util
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "dev" / "fixtures" / "sample_tree.json"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, f"无法加载模块: {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


scan_ads = load_module("scan_ads", ROOT / "skills" / "pikpak-ad-cleaner" / "scripts" / "scan_ads.py")
dedup = load_module("analyze_dupes", ROOT / "skills" / "pikpak-dedup" / "scripts" / "analyze_dupes.py")


def walk_fixture(mod, folder=""):
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    files = []
    counters = {"files": 0, "folders": 0}
    mod.walk(folder, fixture, [], files, counters, 50000)
    return files, counters


class TestAdCleaner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files, cls.counters = walk_fixture(scan_ads)

    def tiers(self):
        buckets = {"high": [], "review": [], "protected": []}
        for rel, f in self.files:
            tier, reasons = scan_ads.classify(f)
            if tier:
                buckets[tier].append(f["id"])
        return buckets

    def test_high_confidence_junk(self):
        """高置信度：.url 快捷方式、含网址/强推广词的文本与图片。"""
        high = self.tiers()["high"]
        for fid in ("f_url", "f_txt", "f_card"):
            self.assertIn(fid, high, f"{fid} 应被判为高置信度垃圾")

    def test_main_video_kept(self):
        """主视频、压缩包、正常海报不得误报。"""
        all_ids = self.tiers()["high"] + self.tiers()["review"] + self.tiers()["protected"]
        for fid in ("f_ep1", "f_zip", "f_poster"):
            self.assertNotIn(fid, all_ids, f"{fid} 不应被判定为广告")

    def test_protected(self):
        """字幕与解压密码文件进入保护清单。"""
        prot = self.tiers()["protected"]
        self.assertIn("f_sub", prot)
        self.assertIn("f_pwd", prot)

    def test_review_items(self):
        """宣传短片与可执行文件进入人工确认。"""
        review = self.tiers()["review"]
        self.assertIn("f_flv", review)
        self.assertIn("f_trojan", review)

    def test_pagination_covered(self):
        """多页目录（fixture 中 Big Buck Bunny 有两页）被完整遍历。"""
        names = {f["name"] for _, f in self.files}
        self.assertIn("installer.exe", names)  # 第二页内容
        self.assertIn("Big.Buck.Bunny.1080p.mkv", names)  # 第一页内容


class TestDedup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files, _ = walk_fixture(dedup)
        # 模拟主流程的分组
        groups = {}
        for rel, f in cls.files:
            if dedup.to_int(f.get("size")) < 102400:
                continue
            key = dedup.normalize_name(f.get("name", ""))
            if len(key.replace(" ", "")) < 4:
                continue
            groups.setdefault(key, []).append(dedup.member_record(rel, f))
        cls.groups = {k: v for k, v in groups.items() if len(v) > 1}

    def test_group_found(self):
        """找到 Sintel 2010 的三个包装版本为一组。"""
        self.assertIn("sintel 2010", self.groups)
        self.assertEqual(len(self.groups["sintel 2010"]), 3)

    def test_language_variant_not_merged(self):
        """国语配音版、S01E01 集数版不得并入同一组（语义 token 未被吞掉）。"""
        for key in self.groups:
            for member in self.groups[key]:
                self.assertNotIn("配音", member["name"])
                self.assertNotIn("s01e01", member["name"].lower())

    def test_newest_keeps_2160p(self):
        """默认 newest 策略保留最新的 2160p 版本，旧的 1080p 版本进入清理候选。"""
        members = self.groups["sintel 2010"]
        keeper = dedup.pick_keeper(members, "newest")
        self.assertEqual(keeper["id"], "f_s2")
        trash = {m["id"] for m in members if m["id"] != keeper["id"]}
        self.assertEqual(trash, {"f_s1", "f_s3"})

    def test_normalize_strips_quality_tokens(self):
        """归一化应剔除清晰度/编码噪音但保留语义。"""
        a = dedup.normalize_name("Sintel 2010 1080p BluRay x264.mkv")
        b = dedup.normalize_name("Sintel.2010.2160p.WEB-DL.HDR.mkv")
        self.assertEqual(a, b)
        c = dedup.normalize_name("Sintel.S01E01.1080p.mkv")
        self.assertNotEqual(a, c)


class TestSafety(unittest.TestCase):
    """安全底线：脚本只通过 subprocess 调用只读的 `pikpak ls`，从不执行删除。"""

    SCRIPT_PATHS = (
        ROOT / "skills" / "pikpak-ad-cleaner" / "scripts" / "scan_ads.py",
        ROOT / "skills" / "pikpak-dedup" / "scripts" / "analyze_dupes.py",
        ROOT / "skills" / "pikpak-storage-report" / "scripts" / "storage_report.py",
    )

    def test_subprocess_only_in_ls_page(self):
        """所有 subprocess 调用必须位于 ls_page 函数内（即只可能调用 pikpak ls）。"""
        import ast
        for path in self.SCRIPT_PATHS:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            # 收集每个 Call 节点 → 所属函数名
            for func in ast.walk(tree):
                if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for node in ast.walk(func):
                    if isinstance(node, ast.Call):
                        name = ast.unparse(node.func)
                        if name.startswith(("subprocess.", "os.system", "os.popen", "os.exec")):
                            self.assertEqual(
                                func.name, "ls_page",
                                f"{path.name} 中 {name} 出现在非只读函数 {func.name} 里",
                            )

    def test_cmd_built_only_for_ls(self):
        """构造给 subprocess 的 cmd 列表必须只含 ls 子命令。"""
        import ast
        for path in self.SCRIPT_PATHS:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            found = False
            for func in ast.walk(tree):
                if isinstance(func, ast.FunctionDef) and func.name == "ls_page":
                    for node in ast.walk(func):
                        if (isinstance(node, ast.Assign)
                                and any(getattr(t, "id", "") == "cmd" for t in node.targets)
                                and isinstance(node.value, ast.List)):
                            seg = ast.get_source_segment(path.read_text(encoding="utf-8"), node.value) or ""
                            found = True
                            self.assertIn('"ls"', seg, f"{path.name}: cmd 列表应包含 ls")
                            for verb in ('"rm"', '"delete"', '"trash"', '"purge"', '"mv"'):
                                self.assertNotIn(verb, seg, f"{path.name}: cmd 不得包含 {verb}")
            self.assertTrue(found, f"{path.name}: 未找到 cmd 构造（测试需更新）")

    def test_fixture_mode_makes_no_subprocess_or_network(self):
        """fixture 离线模式下，脚本的遍历不应触发任何 subprocess 调用。"""
        import subprocess as sp
        storage = load_module("storage_report",
                              ROOT / "skills" / "pikpak-storage-report" / "scripts" / "storage_report.py")
        orig_run = sp.run

        def forbidden(*a, **k):
            raise AssertionError("fixture 模式不应调用 subprocess")

        sp.run = forbidden
        try:
            for mod in (scan_ads, dedup):
                files, _ = walk_fixture(mod)
                self.assertGreater(len(files), 0)
            # storage 的新 API：walk_tree
            fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
            node = storage.walk_tree("", fixture, "根目录", {"files": 0, "folders": 0}, 50000)
            self.assertGreater(node.size, 0)
        finally:
            sp.run = orig_run

    def test_emit_trash_cmd_is_print_only(self):
        """emit-trash-cmd 只打印建议命令，不回读、不执行。"""
        import subprocess as sp
        orig_run = sp.run

        def forbidden(*a, **k):
            raise AssertionError("emit_trash_cmd 不应调用 subprocess")

        sp.run = forbidden
        try:
            for mod in (scan_ads, dedup):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    mod.emit_trash_cmd(["id1", "id2"])
                out = buf.getvalue()
                self.assertIn("pikpak rm id1 id2", out)
        finally:
            sp.run = orig_run


class TestTelegramBot(unittest.TestCase):
    """Telegram bot 离线测试：白名单、消息路由、安全感。"""

    @classmethod
    def setUpClass(cls):
        cls.tg_bot = load_module(
            "tg_bot", ROOT / "skills" / "pikpak-telegram-bot" / "scripts" / "tg_bot.py")
        # 固定 pikpak 调用为 mock
        cls.calls = []

        def fake_pikpak_json(*args, timeout=120):
            TestTelegramBot.calls.append(args)
            if args[0] == "task" and args[1] == "add":
                return {"task_id": "TASK123", "file_name": "demo.mkv"}, None
            if args[0] == "task" and args[1] == "ls":
                return {"tasks": [{"id": "T1", "name": "demo", "status": "Running"}]}, None
            if args[0] == "ls":
                return {"files": [{"name": "video.mp4", "kind": "drive#file"}]}, None
            return {}, None

        cls.tg_bot.pikpak_json = fake_pikpak_json

    def _fake_tg(self):
        class FakeTG:
            def __init__(self):
                self.sent = []

            def send(self, chat_id, text):
                self.sent.append((str(chat_id), text))
        return FakeTG()

    def test_allowed_parse(self):
        self.assertEqual(self.tg_bot.parse_allowed("1, 2 ,3"), {"1", "2", "3"})
        self.assertEqual(self.tg_bot.parse_allowed(""), set())

    def test_unauthorized_silently_rejected(self):
        tg = self._fake_tg()
        self.tg_bot.process_message(tg, {"chat": {"id": 999}, "text": "/help"}, {"111"})
        self.assertEqual(tg.sent, [])

    def test_magnet_autosave(self):
        tg = self._fake_tg()
        self.tg_bot.process_message(
            tg, {"chat": {"id": 111}, "text": "magnet:?xt=urn:btih:abc"}, {"111"})
        self.assertTrue(any("已开始转存" in t for _, t in tg.sent))

    def test_bot_source_has_no_rm(self):
        src = (ROOT / "skills" / "pikpak-telegram-bot" / "scripts" / "tg_bot.py").read_text(encoding="utf-8")
        self.assertNotIn('"rm"', src)
        self.assertNotIn("'rm'", src)


class TestBatchBackup(unittest.TestCase):
    """批量备份：路径穿越清洗 + dry-run 安全。"""

    @classmethod
    def setUpClass(cls):
        cls.bb = load_module("batch_backup",
                             ROOT / "skills" / "pikpak-batch-backup" / "scripts" / "batch_backup.py")

    def test_sanitize_blocks_path_traversal(self):
        for evil in ["../../etc/passwd", "..\\..\\windows", "/etc/hosts", ".ssh/id_rsa", "..", ""]:
            s = self.bb.sanitize(evil)
            self.assertNotIn("/", s)
            self.assertNotIn("\\", s)
            self.assertFalse(s.startswith("."), f"{evil!r} -> {s!r} 不应以点开头")
            self.assertTrue(s)

    def test_sanitize_keeps_normal(self):
        self.assertEqual(self.bb.sanitize("normal.mp4"), "normal.mp4")

    def test_writes_only_download_no_delete(self):
        src = (ROOT / "skills" / "pikpak-batch-backup" / "scripts" / "batch_backup.py").read_text(encoding="utf-8")
        # 备份脚本只允许 download / ls 两个子命令
        for forbidden in ['"rm"', '"delete"', '"trash"', '"purge"', '"mv"']:
            self.assertNotIn(forbidden, src)


class TestTaskGuardian(unittest.TestCase):
    """任务守护：通知触发与去重、状态持久化。"""

    @classmethod
    def setUpClass(cls):
        import tempfile
        cls.tg = load_module("task_guardian",
                             ROOT / "skills" / "pikpak-task-guardian" / "scripts" / "task_guardian.py")
        cls.tmp = Path(tempfile.mkdtemp())
        cls.tg.STATE_PATH = cls.tmp / "state.json"
        cls.tg.WATCH_PATH = cls.tmp / "watch.json"
        cls.notes = []
        cls.tg.notify = lambda title, msg: cls.notes.append((title, msg))
        cls.fixture = {"tasks": [
            {"id": "t1", "name": "running", "status": "Running"},
            {"id": "t2", "name": "done", "status": "Complete"},
            {"id": "t3", "name": "failed", "status": "Error"},
        ]}

    def test_notifications_and_dedup(self):
        self.notes.clear()
        state = self.tg.once(self.fixture)
        self.assertTrue(any("完成" in t for t, _ in self.notes))
        self.assertTrue(any("失败" in t for t, _ in self.notes))
        # 第二次不应重复通知
        self.notes.clear()
        self.tg.once(self.fixture)
        self.assertEqual(self.notes, [])
        saved = json.loads((self.tmp / "state.json").read_text(encoding="utf-8"))
        self.assertIn("t2", saved["seen_done"])

    def test_guardian_no_file_ops(self):
        src = (ROOT / "skills" / "pikpak-task-guardian" / "scripts" / "task_guardian.py").read_text(encoding="utf-8")
        # 守护脚本只允许 task 子命令 + osascript，不得有文件删除
        for forbidden in ['"files"', '"rm", "--delete-files"', '"batchDelete"']:
            self.assertNotIn(forbidden, src)


class TestShareAudit(unittest.TestCase):
    """分享审计：过期/临期/零访问标记。"""

    @classmethod
    def setUpClass(cls):
        cls.sa = load_module("share_audit",
                             ROOT / "skills" / "pikpak-share-manager" / "scripts" / "share_audit.py")

    def test_audit_flags(self):
        fixture = json.loads((ROOT / "dev" / "fixtures" / "shares.json").read_text(encoding="utf-8"))
        report = self.sa.audit(fixture["shares"])
        by_id = {r["share_id"]: r for r in report}
        self.assertIn("已过期", by_id["SHARE_EXPIRED_1"]["flags"])
        self.assertTrue(any("7 天内" in f for f in by_id["SHARE_SOON_1"]["flags"]))
        self.assertTrue(any("零访问" in f for f in by_id["SHARE_ZERO_1"]["flags"]))
        self.assertTrue(any("长期有效" in f for f in by_id["SHARE_FOREVER_1"]["flags"]))

    def test_parse_time_formats(self):
        p = self.sa.parse_time
        self.assertIsNotNone(p("2026-10-01T00:00:00+00:00"))
        self.assertIsNotNone(p(1759276800))       # 秒时间戳
        self.assertIsNotNone(p(1759276800000))    # 毫秒时间戳
        self.assertIsNone(p("not a time"))
        self.assertIsNone(p(None))

    def test_audit_readonly(self):
        src = (ROOT / "skills" / "pikpak-share-manager" / "scripts" / "share_audit.py").read_text(encoding="utf-8")
        # 只允许 share ls；不得创建/删除
        self.assertIn('"share", "ls"', src)
        for forbidden in ['"create"', '"rm"', '"delete"']:
            # 允许出现在注释/建议命令字符串中，但不得出现在 subprocess 调用的参数列表
            self.assertNotIn(f'[os.environ.get("PIKPAK_BIN", "pikpak"), "share", {forbidden}]', src)


class TestNewSkills(unittest.TestCase):
    """新 skill：search 与 magnet-pipeline 的核心行为。"""

    @classmethod
    def setUpClass(cls):
        cls.search = load_module("search_files",
                                 ROOT / "skills" / "pikpak-search" / "scripts" / "search_files.py")
        cls.pipeline = load_module("magnet_pipeline",
                                   ROOT / "skills" / "pikpak-magnet-pipeline" / "scripts" / "magnet_pipeline.py")

    def test_search_finds_by_keyword(self):
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        files = []
        self.search.walk("", fixture, [], files, {"files": 0, "folders": 0}, 50000)
        self.assertGreater(len(files), 0)
        # 关键词匹配
        hits = []
        for rel, f in files:
            name = f.get("name", "")
            path = "/".join(rel + [name])
            if self.search.match(name, path, ["sintel"], "or", False):
                hits.append(name)
        self.assertGreaterEqual(len(hits), 4)  # fixture 里多个 Sintel 版本

    def test_search_and_mode(self):
        m = self.search.match
        self.assertTrue(m("Sintel.2010.1080p.mkv", "x", ["sintel", "1080"], "and", False))
        self.assertFalse(m("Sintel.2010.2160p.mkv", "x", ["sintel", "1080"], "and", False))

    def test_pipeline_step4_prints_only(self):
        """step4-cleanup 只打印建议命令，不执行。"""
        import subprocess as sp
        orig_run = sp.run

        def forbidden(*a, **k):
            raise AssertionError("step4 不应调用 subprocess")

        sp.run = forbidden
        try:
            import io
            from contextlib import redirect_stdout
            import argparse
            ns = argparse.Namespace(file_ids=["F1", "F2"])
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.pipeline.step4_cleanup(ns)
            out = buf.getvalue()
            self.assertIn("pikpak rm F1", out)
            self.assertIn("pikpak rm F2", out)
            self.assertIn("请确认后手动执行", out)
        finally:
            sp.run = orig_run

    def test_pipeline_no_delete_in_source(self):
        src = (ROOT / "skills" / "pikpak-magnet-pipeline" / "scripts" / "magnet_pipeline.py").read_text(encoding="utf-8")
        import ast
        tree = ast.parse(src)
        for func in ast.walk(tree):
            if isinstance(func, ast.FunctionDef):
                for node in ast.walk(func):
                    if isinstance(node, ast.Call) and "subprocess" in ast.unparse(node.func):
                        seg = ast.unparse(node)
                        self.assertNotIn('"rm"', seg, f"{func.name} 不应含删除调用")


class TestWebdavGuide(unittest.TestCase):
    """WebDAV 指南：检查脚本的会员状态判断。"""

    @classmethod
    def setUpClass(cls):
        cls.wd = load_module("webdav_check",
                             ROOT / "skills" / "pikpak-webdav-guide" / "scripts" / "webdav_check.py")

    def test_member_detection(self):
        """会员判断逻辑：通过 pikpak_json 的 mock 验证。"""
        import io
        from contextlib import redirect_stdout

        orig = self.wd.pikpak_json

        # 会员场景
        self.wd.pikpak_json = lambda *a, **k: ({
            "logged_in": True,
            "vip": {"type": "Platinum", "status": "ok", "expire_time": "2027-01-01T00:00:00+08:00"},
            "storage": {"total": "10 TB", "used": "1 TB", "in_trash": "0 B"},
        }, None)
        try:
            import sys as _sys
            _sys.argv = ["webdav_check.py", "--json"]
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.wd.main()
            out = json.loads(buf.getvalue())
            self.assertTrue(out["can_use_webdav"])
        finally:
            self.wd.pikpak_json = orig

        # 非会员场景
        self.wd.pikpak_json = lambda *a, **k: ({
            "logged_in": True,
            "vip": {"type": "Free", "status": "", "expire_time": ""},
            "storage": {"total": "6 GB", "used": "1 GB", "in_trash": "0 B"},
        }, None)
        try:
            import sys as _sys
            _sys.argv = ["webdav_check.py", "--json"]
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.wd.main()
            out = json.loads(buf.getvalue())
            self.assertFalse(out["can_use_webdav"])
        finally:
            self.wd.pikpak_json = orig

    def test_readonly(self):
        """webdav_check 只调用 auth status（只读）。"""
        import ast
        src = (ROOT / "skills" / "pikpak-webdav-guide" / "scripts" / "webdav_check.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and "pikpak_json" in ast.unparse(node.func):
                calls.append(ast.unparse(node))
        # 所有 pikpak_json 调用都必须是 auth status（或含 status 的只读调用）
        self.assertTrue(calls, "应存在 pikpak_json 调用")
        for c in calls:
            self.assertIn("status", c, f"非只读调用：{c}")
            self.assertNotIn('"rm"', c)
            self.assertNotIn('"task"', c.lower().replace("auth", ""))


class TestSpeedGuide(unittest.TestCase):
    """速度指南：诊断脚本逻辑。"""

    @classmethod
    def setUpClass(cls):
        cls.sg = load_module("speed_diag",
                             ROOT / "skills" / "pikpak-speed-guide" / "scripts" / "speed_diag.py")

    def test_recommend_all_ok(self):
        results = [
            {"host": "a.com", "ok": True, "total_ms": 200, "dns_ms": 20},
            {"host": "b.com", "ok": True, "total_ms": 300, "dns_ms": 25},
        ]
        tips = self.sg.recommend(results)
        self.assertTrue(any("换网络" in t for t in tips))

    def test_recommend_unreachable(self):
        results = [
            {"host": "a.com", "ok": False, "dns_error": "timeout"},
        ]
        tips = self.sg.recommend(results)
        self.assertTrue(any("不可达" in t for t in tips))

    def test_recommend_slow(self):
        results = [
            {"host": "slow.com", "ok": True, "total_ms": 3000, "dns_ms": 50},
        ]
        tips = self.sg.recommend(results)
        self.assertTrue(any("QoS" in t for t in tips))

    def test_diag_is_readonly(self):
        """诊断脚本不应含任何写操作或凭据接触。"""
        src = (ROOT / "skills" / "pikpak-speed-guide" / "scripts" / "speed_diag.py").read_text(encoding="utf-8")
        for forbidden in ["pikpak rm", "task add", "auth login", "password", "DELETE"]:
            self.assertNotIn(forbidden, src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
