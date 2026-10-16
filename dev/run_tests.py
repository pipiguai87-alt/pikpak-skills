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


if __name__ == "__main__":
    unittest.main(verbosity=2)
