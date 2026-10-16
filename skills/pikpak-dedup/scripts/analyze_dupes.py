#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PikPak 重复/新旧版本资源分析器（只读；绝不执行删除）。

在同一目录树内按「文件名归一化」把疑似同一资源的多个版本分组，按策略给出
「保留最新/最大/最高分辨率」的清理建议，输出报告。确认后由 AI 助手或用户
执行 `pikpak rm <id...>` 将旧版本移入回收站（可恢复）。

归一化规则（保守设计）：
  - 统一小写、去掉扩展名、括号/分隔符转空格
  - 去掉清晰度/编码/来源/音轨等「包装噪音」词（1080p、x264、WEB-DL、HEVC…）
  - 保留语义信息：集数（S01E02、第01集）、语言音轨（国语/粤语/中字）、
    版本说明（未删减/重制版等）——这些不同则视为不同资源，不合并

用法：
  python3 analyze_dupes.py --folder <folderID>
  python3 analyze_dupes.py --folder <folderID> --policy newest
  python3 analyze_dupes.py --folder <folderID> --json report.json
  python3 analyze_dupes.py --folder <folderID> --emit-trash-cmd
  python3 analyze_dupes.py --from-fixture dev/fixtures/sample_tree.json --folder ""

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
from datetime import datetime, timezone
from typing import NoReturn

# ---------------------------------------------------------------------------
# 归一化配置（可按需调整；调整后请重跑 dev/run_tests.py）
# ---------------------------------------------------------------------------

# 会被剔除的「包装噪音」词（全小写）。故意不含语言/版本语义词。
QUALITY_TOKENS = {
    "1080p", "720p", "2160p", "4k", "8k", "uhd", "fhd", "hd", "sd",
    "x264", "x265", "h264", "h265", "hevc", "avc", "av1", "xvid", "divx",
    "web", "webdl", "webrip", "bluray", "bdrip", "brrip", "bd", "remux",
    "hdrip", "dvdrip", "hdtv", "hdr", "hdr10", "dv", "dolby", "atmos",
    "10bit", "8bit", "hi10p", "aac", "ac3", "eac3", "dts", "flac", "mp3",
    "truehd", "repack", "proper", "dl", "web-dl",
}

KNOWN_EXTS = {
    "mp4", "mkv", "avi", "mov", "wmv", "flv", "ts", "m2ts", "rmvb", "webm",
    "mpg", "mpeg", "m4v", "3gp", "vob", "f4v", "mp3", "flac", "wav", "m4a",
    "aac", "ogg", "jpg", "jpeg", "png", "gif", "webp", "bmp", "txt", "url",
    "html", "htm", "srt", "ass", "vtt", "zip", "rar", "7z", "iso", "pdf",
    "nfo", "sub", "idx", "sup", "md",
}

DOMAIN_TOKEN_RE = re.compile(r"^(?:https?://)?(?:[\w-]+\.)+[a-z]{2,6}(?:/\S*)?$")

# ---------------------------------------------------------------------------


def die(msg: str, code: int = 1) -> NoReturn:
    print(f"错误：{msg}", file=sys.stderr)
    raise SystemExit(code)


def to_int(value) -> int:
    if value is None:
        return 0
    m = re.search(r"\d+", str(value))
    return int(m.group(0)) if m else 0


def human_size(n: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    if n <= 0:
        return "0 B"
    i = 0
    value = float(n)
    while value >= 1024 and i < len(units) - 1:
        value /= 1024.0
        i += 1
    return f"{value:.0f} {units[i]}" if i == 0 else f"{value:.1f} {units[i]}"


def normalize_name(name: str) -> str:
    """把文件名归一化为「资源标识 key」。保守：只剔除包装噪音。"""
    n = name.lower()
    # 去扩展名（仅识别已知扩展名，避免误删名称尾段）
    m = re.search(r"\.([a-z0-9]{1,8})$", n)
    if m and m.group(1) in KNOWN_EXTS:
        n = n[: m.start()]
    # 括号、分隔符统一为空格
    n = re.sub(r"[\[\](){}【】（）《》]", " ", n)
    n = re.sub(r"[_\-\.\+·]+", " ", n)
    tokens = n.split()
    kept = []
    for t in tokens:
        if DOMAIN_TOKEN_RE.match(t):
            continue  # 发布站域名 token
        if t in QUALITY_TOKENS:
            continue
        kept.append(t)
    return " ".join(kept).strip()


def parse_time(value: str) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    text = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------


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
        die("无法解析 pikpak ls 输出为 JSON。")


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


def resolution_of(f: dict) -> int:
    params = f.get("params") or {}
    w = to_int(params.get("width"))
    h = to_int(params.get("height"))
    return max(w, h)


def member_record(rel_path, f):
    params = f.get("params") or {}
    w = to_int(params.get("width"))
    h = to_int(params.get("height"))
    res = f"{w}x{h}" if w or h else ""
    return {
        "id": f.get("id", ""),
        "name": f.get("name", ""),
        "path": "/".join(rel_path + [f.get("name", "")]),
        "size": to_int(f.get("size")),
        "size_h": human_size(to_int(f.get("size"))),
        "modified": f.get("modified_time", ""),
        "created": f.get("created_time", ""),
        "resolution": res,
        "hash": (f.get("hash") or "").strip(),
    }


def pick_keeper(members, policy: str):
    if policy == "largest":
        return max(members, key=lambda m: (m["size"], parse_time(m["modified"])))
    if policy == "highest-res":
        return max(members, key=lambda m: (to_int(m["resolution"].split("x")[0]) if "x" in m["resolution"] else 0,
                                           m["size"], parse_time(m["modified"])))
    # 默认 newest：最新修改（导入）时间优先
    return max(members, key=lambda m: (parse_time(m["modified"]), m["size"]))


def emit_trash_cmd(ids):
    for i in range(0, len(ids), 30):
        batch = ids[i:i + 30]
        print("pikpak rm " + " ".join(batch))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--folder", default="", help='要扫描的目录 ID（默认根目录，即空字符串）')
    ap.add_argument("--from-fixture", help="离线模式：从 JSON fixture 读取目录数据（测试用，不访问网络）")
    ap.add_argument("--policy", choices=["newest", "largest", "highest-res"], default="newest",
                    help="保留策略：newest=保留最新修改（默认）；largest=保留最大；highest-res=保留最高分辨率")
    ap.add_argument("--min-size", type=int, default=102400,
                    help="只分析不小于该字节数的文件（默认 102400，即 100 KB；避免把小图/说明文本卷入分组）")
    ap.add_argument("--json", dest="json_out", help='把完整报告写入该文件（"-" 输出到标准输出）')
    ap.add_argument("--emit-trash-cmd", action="store_true",
                    help="打印把各分组「旧版本」移入回收站的 pikpak rm 命令（供人工确认后执行）")
    ap.add_argument("--max-items", type=int, default=50000, help="最大扫描文件数安全上限（默认 50000）")
    args = ap.parse_args()

    fixture = None
    if args.from_fixture:
        with open(args.from_fixture, encoding="utf-8") as fh:
            fixture = json.load(fh)

    files = []
    counters = {"files": 0, "folders": 0}
    walk(args.folder, fixture, [], files, counters, args.max_items)

    groups = {}
    for rel_path, f in files:
        size = to_int(f.get("size"))
        if size < args.min_size:
            continue
        key = normalize_name(f.get("name", ""))
        if len(key.replace(" ", "")) < 4:
            continue
        groups.setdefault(key, []).append(member_record(rel_path, f))

    dup_groups = []
    for key in sorted(groups):
        members = groups[key]
        if len(members) < 2:
            continue
        keeper = pick_keeper(members, args.policy)
        others = [m for m in members if m["id"] != keeper["id"]]
        hashes = {m["hash"] for m in members if m["hash"]}
        exact = len(hashes) == 1 and all(m["hash"] for m in members)
        dup_groups.append({
            "key": key,
            "count": len(members),
            "policy": args.policy,
            "keeper": keeper,
            "trash_candidates": others,
            "note": "内容哈希相同（完全重复）" if exact else "",
        })

    report = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "folder": args.folder,
        "source": "fixture" if fixture is not None else "live",
        "policy": args.policy,
        "scanned_files": counters["files"],
        "scanned_folders": counters["folders"],
        "min_size": args.min_size,
        "groups": dup_groups,
    }

    print(f"扫描完成：目录 {counters['folders']} 个，文件 {counters['files']} 个"
          + ("（离线 fixture 模式）" if fixture is not None else ""))
    if not dup_groups:
        print("\n未发现疑似重复/多版本资源。")
    else:
        total_trash = sum(len(g["trash_candidates"]) for g in dup_groups)
        print(f"\n发现 {len(dup_groups)} 组疑似重复资源，建议清理 {total_trash} 个旧版本"
              f"（策略：保留{'最新' if args.policy == 'newest' else '最大' if args.policy == 'largest' else '最高分辨率'}）：\n")
        for g in dup_groups:
            note = f"（{g['note']}）" if g["note"] else ""
            print(f"【{g['key']}】{g['count']} 个版本{note}")
            k = g["keeper"]
            print(f"  ✓ 保留： {k['size_h']:>10}  {k['modified'][:16]}  {k['path']}"
                  + (f"  [{k['resolution']}]" if k["resolution"] else ""))
            for t in g["trash_candidates"]:
                print(f"  ✗ 清理： {t['size_h']:>10}  {t['modified'][:16]}  {t['path']}"
                      + (f"  [{t['resolution']}]" if t["resolution"] else ""))
            print()
        print("提示：确认清单后再执行删除；`pikpak rm` 只是移入回收站（可恢复）。")
        print("      如策略不合适可用 --policy largest / highest-res 重跑。")

    if args.json_out:
        text = json.dumps(report, ensure_ascii=False, indent=2)
        if args.json_out == "-":
            print(text)
        else:
            with open(args.json_out, "w", encoding="utf-8") as fh:
                fh.write(text)
            print(f"\n报告已写入：{args.json_out}")

    if args.emit_trash_cmd:
        ids = [t["id"] for g in dup_groups for t in g["trash_candidates"] if t["id"]]
        if not ids:
            print("\n（没有可清理项，无命令可生成。）")
        else:
            print(f"\n以下命令会把这 {len(ids)} 个旧版本移入回收站（请用户确认后再执行）：")
            emit_trash_cmd(ids)


if __name__ == "__main__":
    main()
