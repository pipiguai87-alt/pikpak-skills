#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PikPak 广告/垃圾文件扫描器（只读；绝不执行删除）。

通过 `pikpak ls --raw` 遍历云端目录树，按「扩展名 + 文件名特征 + 体积/时长」
识别磁力/种子资源包中常见的推广垃圾文件，输出分级报告：

  high      高置信度垃圾：.url/.website 快捷方式、文件名含网址或强推广词的文本/图片等
  review    需要人工确认：疑似宣传短片、可执行文件、弱推广词文件等
  protected 明确保护、不建议删除：字幕文件、解压密码/说明文件

确认后由 AI 助手或用户执行 `pikpak rm <id...>` 将文件移入回收站（可恢复）。
本工具不做任何删除或修改。

用法：
  python3 scan_ads.py --folder <folderID>
  python3 scan_ads.py --folder <folderID> --json report.json
  python3 scan_ads.py --folder <folderID> --emit-trash-cmd high
  python3 scan_ads.py --from-fixture dev/fixtures/sample_tree.json --folder fld1

环境变量：
  PIKPAK_BIN      pikpak 可执行文件路径（默认 "pikpak"）
  PIKPAK_TIMEOUT  单次 ls 超时秒数（默认 90）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from typing import NoReturn

# ---------------------------------------------------------------------------
# 规则常量（可按需调整；调整后请重跑 dev/run_tests.py）
# ---------------------------------------------------------------------------

VIDEO_EXT = {
    "mp4", "mkv", "avi", "mov", "wmv", "flv", "ts", "m2ts", "rmvb", "webm",
    "mpg", "mpeg", "m4v", "3gp", "vob", "f4v",
}
JUNK_EXT = {
    "txt", "html", "htm", "md", "ico",
    "jpg", "jpeg", "png", "gif", "webp", "bmp", "svg",
}
SUBTITLE_EXT = {"srt", "ass", "ssa", "vtt", "sub", "idx", "smi", "sup"}
ARCHIVE_EXT = {"zip", "rar", "7z", "tar", "gz", "bz2", "xz", "iso"}
DANGER_EXT = {"exe", "msi", "bat", "cmd", "vbs", "scr", "apk", "dmg", "pkg", "jar"}
SHORTCUT_EXT = {"url", "website", "lnk", "webloc", "desktop"}

PASSWORD_KEYS = ["密码", "password", "passwd", "解压码"]

# 强推广词：单独命中即可怀疑（配合扩展名规则分级）
STRONG_KEYS = [
    "最新地址", "永久地址", "永久域名", "发布页", "发布站", "网址导航", "导航页",
    "防失联", "防走失", "不迷路", "收藏本站", "备用网址", "备用地址",
    "点击进入", "点击查看", "更多精彩", "更多资源", "更多福利",
    "加群", "进群", "入群", "扫码", "二维码", "加微信", "微信号",
    "客服", "官网", "官方网址", "免费领取", "领取福利", "免费观看",
    "影视大全", "看片神器", "看片", "广告", "宣传", "推广", "赞助", "邀请码",
]
# 弱推广词：需多个同时出现或配合其它信号
WEAK_KEYS = ["网址", "备用", "福利", "领取", "神器", "入口", "地址", "域名", "发布"]

DOMAIN_RE = re.compile(
    r"(?:https?://|www\.)"
    r"|(?<![\w@])[\w-]{2,}\.(?:com|net|org|cc|xyz|top|vip|icu|me|info|club|site"
    r"|online|shop|live|tv|app|link|pro|fun|pw|cyou|sbs|uno)(?![\w])",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------


class Fatal(Exception):
    """无法继续的错误。"""


def die(msg: str, code: int = 1) -> NoReturn:
    print(f"错误：{msg}", file=sys.stderr)
    raise SystemExit(code)


def file_ext(name: str) -> str:
    m = re.search(r"\.([A-Za-z0-9]{1,8})$", name)
    return m.group(1).lower() if m else ""


def to_int(value) -> int:
    if value is None:
        return 0
    m = re.search(r"\d+", str(value))
    return int(m.group(0)) if m else 0


def human_size(n: int) -> str:
    if n <= 0:
        return "0 B"
    units = ("B", "KB", "MB", "GB", "TB")
    i = 0
    value = float(n)
    while value >= 1024 and i < len(units) - 1:
        value /= 1024.0
        i += 1
    return f"{value:.0f} {units[i]}" if i == 0 else f"{value:.1f} {units[i]}"


def ls_page(folder_id: str, fixture: "dict | None", page_token: str = "") -> dict:
    """取一页目录列表。fixture 非 None 时离线读取（按 folder_id → page_token 键控）。"""
    if fixture is not None:
        node = fixture.get(folder_id) or {}
        return dict(node.get(page_token) or {"files": [], "next_page_token": ""})

    cmd = [os.environ.get("PIKPAK_BIN", "pikpak"), "ls", "--raw", "-l", "500"]
    if folder_id:
        cmd += ["-p", folder_id]
    if page_token:
        cmd += ["-t", page_token]
    timeout = int(os.environ.get("PIKPAK_TIMEOUT", "90"))
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        die(f"找不到 pikpak 命令（{cmd[0]}）。请先安装 PikPak CLI"
            "（官方安装页：https://mypikpak.com/cli）。")
    except subprocess.TimeoutExpired:
        die(f"pikpak ls 超时（{timeout}s），请检查网络后重试。")
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        hint = ""
        if "UNAUTHENTICATED" in err.upper():
            hint = " 请先运行 `pikpak auth login` 登录。"
        die(f"pikpak ls 调用失败：{err[:400]}{hint}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        die("无法解析 pikpak ls 输出为 JSON（如为旧版本请先 `pikpak version --check`）。")


def walk(folder_id: str, fixture: "dict | None", rel_path, out, counters, max_items: int) -> None:
    token = ""
    while True:
        page = ls_page(folder_id, fixture, token)
        for f in page.get("files", []):
            name = f.get("name", "")
            kind = f.get("kind")
            if kind == "drive#folder":
                counters["folders"] += 1
                if counters["folders"] > 5000:
                    die("目录数量超过安全上限（5000），已中止。请缩小扫描范围。")
                walk(f.get("id", ""), fixture, rel_path + [name], out, counters, max_items)
            elif kind == "drive#file":
                counters["files"] += 1
                if counters["files"] > max_items:
                    die(f"文件数量超过安全上限（{max_items}），已中止。请缩小扫描范围。")
                out.append((rel_path, f))
        token = page.get("next_page_token", "") or ""
        if not token:
            break


def classify(f: dict):
    """返回 (tier, reasons)；tier 为 high/review/protected/None。"""
    name = f.get("name", "")
    ext = file_ext(name)
    size = to_int(f.get("size"))
    dur = to_int((f.get("params") or {}).get("duration"))
    low = name.lower()

    # 1) 保护清单（优先于一切垃圾判定）
    if ext in SUBTITLE_EXT:
        return "protected", ["字幕文件"]
    if ext == "txt" and any(k in name or k in low for k in PASSWORD_KEYS):
        return "protected", ["疑似解压密码/说明文件"]
    if ext in ARCHIVE_EXT:
        return None, []  # 压缩包通常是资源本体，不参与广告判定

    # 2) 快捷方式文件
    if ext in SHORTCUT_EXT:
        return "high", [f".{ext} 网站快捷方式（典型引流文件）"]

    junkish = ext in JUNK_EXT
    strong = [k for k in STRONG_KEYS if k in name or k in low]
    weak = [k for k in WEAK_KEYS if k in name or k in low]
    has_domain = bool(DOMAIN_RE.search(name))

    # 3) 可执行文件：媒体包里出现即可疑，交给人工确认
    if ext in DANGER_EXT:
        why = "可执行文件（注意安全）"
        if strong:
            why += f"，含推广词：{'、'.join(strong[:3])}"
        return "review", [why]

    # 4) 文本/图片类
    if junkish:
        if has_domain:
            return "high", ["文件名含网址"]
        if strong:
            return "high", [f"含推广词：{'、'.join(strong[:3])}"]
        if len(weak) >= 2:
            return "review", [f"含多个弱推广词：{'、'.join(weak[:3])}"]
        return None, []

    # 5) 视频：只有明显宣传短片特征才进 review
    if ext in VIDEO_EXT:
        small = (size and size <= 300 * 1024 * 1024) or (dur and dur <= 180)
        if strong and small:
            return "review", ["疑似宣传短片（含推广词且体积/时长很小）"]
        return None, []

    # 6) 其它扩展名：仅强推广词兜底
    if strong:
        return "review", [f"含推广词：{'、'.join(strong[:3])}"]
    return None, []


def item_record(rel_path, f, tier, reasons):
    name = f.get("name", "")
    return {
        "id": f.get("id", ""),
        "name": name,
        "path": "/".join(rel_path + [name]),
        "size": to_int(f.get("size")),
        "size_h": human_size(to_int(f.get("size"))),
        "ext": file_ext(name),
        "tier": tier,
        "reasons": reasons,
        "modified": f.get("modified_time", ""),
    }


def emit_trash_cmd(ids):
    for i in range(0, len(ids), 30):
        batch = ids[i:i + 30]
        print("pikpak rm " + " ".join(batch))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--folder", default="", help='要扫描的目录 ID（默认根目录，即空字符串）')
    ap.add_argument("--from-fixture", help="离线模式：从 JSON fixture 读取目录数据（测试用，不访问网络）")
    ap.add_argument("--json", dest="json_out", help='把完整报告写入该文件（"-" 输出到标准输出）')
    ap.add_argument("--emit-trash-cmd", choices=["high", "review"],
                    help="打印对应分级的 pikpak rm 命令（供人工确认后执行；本工具不代为执行）")
    ap.add_argument("--max-items", type=int, default=50000, help="最大扫描文件数安全上限（默认 50000）")
    args = ap.parse_args()

    fixture = None
    if args.from_fixture:
        with open(args.from_fixture, encoding="utf-8") as fh:
            fixture = json.load(fh)

    files = []
    counters = {"files": 0, "folders": 0}
    walk(args.folder, fixture, [], files, counters, args.max_items)

    buckets = {"high": [], "review": [], "protected": []}
    for rel_path, f in files:
        tier, reasons = classify(f)
        if tier:
            buckets[tier].append(item_record(rel_path, f, tier, reasons))

    report = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "folder": args.folder,
        "source": "fixture" if fixture is not None else "live",
        "scanned_files": counters["files"],
        "scanned_folders": counters["folders"],
        "high": buckets["high"],
        "review": buckets["review"],
        "protected": buckets["protected"],
    }

    print(f"扫描完成：目录 {counters['folders']} 个，文件 {counters['files']} 个"
          + ("（离线 fixture 模式）" if fixture is not None else ""))
    for tier, label in (("high", "高置信度垃圾（建议清理）"),
                        ("review", "待人工确认"), ("protected", "受保护（不建议删除）")):
        items = buckets[tier]
        if items:
            print(f"\n[{label}] {len(items)} 个：")
            for it in items:
                print(f"  {it['size_h']:>10}  {it['path']}  —— {'；'.join(it['reasons'])}")
    if not any(buckets.values()):
        print("\n未发现疑似广告/垃圾文件。")
    else:
        print("\n提示：确认清单后再执行删除；`pikpak rm` 只是移入回收站（可恢复）。")

    if args.json_out:
        text = json.dumps(report, ensure_ascii=False, indent=2)
        if args.json_out == "-":
            print(text)
        else:
            with open(args.json_out, "w", encoding="utf-8") as fh:
                fh.write(text)
            print(f"\n报告已写入：{args.json_out}")

    if args.emit_trash_cmd:
        ids = [it["id"] for it in buckets[args.emit_trash_cmd] if it["id"]]
        if not ids:
            print(f"\n（{args.emit_trash_cmd} 分级为空，无命令可生成。）")
        else:
            print(f"\n以下命令会把这 {len(ids)} 个文件移入回收站（请用户确认后再执行）：")
            emit_trash_cmd(ids)


if __name__ == "__main__":
    try:
        main()
    except Fatal as exc:
        die(str(exc))
