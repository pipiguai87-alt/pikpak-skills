#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PikPak 媒体库索引生成器（只读）。

扫描目录树，提取视频元数据（时长/分辨率/编码/来源），生成 Markdown 或 CSV 索引。

用法：
  python3 media_index.py --folder "" --output /tmp/media_index.md
  python3 media_index.py --folder "" --format csv --output /tmp/media.csv
  python3 media_index.py --folder "" --filter 4k
  python3 media_index.py --from-fixture dev/fixtures/sample_tree.json --folder ""

环境变量：
  PIKPAK_BIN      pikpak 可执行文件路径（默认 "pikpak"）
  PIKPAK_TIMEOUT  单次 ls 超时秒数（默认 90）
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from typing import NoReturn


def die(msg: str, code: int = 1) -> NoReturn:
    print(f"错误：{msg}", file=sys.stderr)
    raise SystemExit(code)


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


def hms(seconds: int) -> str:
    if seconds <= 0:
        return "-"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def ls_page(folder_id: str, fixture: "dict | None", page_token: str = "") -> dict:
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
        die("找不到 pikpak 命令。请先安装 PikPak CLI。")
    except subprocess.TimeoutExpired:
        die(f"pikpak ls 超时（{timeout}s）。")
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        hint = " 请先运行 `pikpak auth login` 登录。" if "UNAUTHENTICATED" in err.upper() else ""
        die(f"pikpak ls 调用失败：{err[:400]}{hint}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        die("无法解析 pikpak ls 输出。")


def walk(folder_id: str, fixture: "dict | None", rel_path, out, counters, max_items: int) -> None:
    token = ""
    while True:
        page = ls_page(folder_id, fixture, token)
        for f in page.get("files", []):
            kind = f.get("kind")
            if kind == "drive#folder":
                counters["folders"] += 1
                if counters["folders"] > 5000:
                    die("目录数量超过安全上限。")
                walk(f.get("id", ""), fixture, rel_path + [f.get("name", "")], out, counters, max_items)
            elif kind == "drive#file":
                counters["files"] += 1
                if counters["files"] > max_items:
                    die(f"文件数超过安全上限（{max_items}）。")
                out.append((rel_path, f))
        token = page.get("next_page_token", "") or ""
        if not token:
            break


def video_record(rel_path, f):
    params = f.get("params") or {}
    duration = to_int(params.get("duration"))
    width = to_int(params.get("width"))
    height = to_int(params.get("height"))
    long_edge = max(width, height)
    codec = ""
    for media in f.get("medias") or []:
        v = (media.get("video") or {})
        if v.get("video_codec"):
            codec = v["video_codec"]
            break
    source = params.get("url") or ""
    if source.startswith("magnet:"):
        source = source[:60] + ("…" if len(source) > 60 else "")
    return {
        "name": f.get("name", ""),
        "path": "/".join(rel_path),
        "size": to_int(f.get("size")),
        "duration": duration,
        "resolution": f"{width}x{height}" if width or height else "",
        "long_edge": long_edge,
        "is_4k": long_edge >= 3840,
        "codec": codec,
        "source": source,
        "pending": duration == 0,  # 服务端尚未完成转码
        "modified": f.get("modified_time", ""),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--folder", default="", help="要扫描的目录 ID（默认根目录）")
    ap.add_argument("--from-fixture", help="离线模式（测试用）")
    ap.add_argument("--format", choices=["md", "csv"], default="md", help="输出格式（默认 md）")
    ap.add_argument("--output", help="写入文件（默认 stdout）")
    ap.add_argument("--filter", choices=["all", "4k", "hd", "short"], default="all",
                    help="筛选：all/4k（长边≥3840）/hd（长边≥1280）/short（≤1小时）")
    ap.add_argument("--max-items", type=int, default=50000, help="文件数安全上限")
    args = ap.parse_args()

    fixture = None
    if args.from_fixture:
        with open(args.from_fixture, encoding="utf-8") as fh:
            fixture = json.load(fh)

    files = []
    counters = {"files": 0, "folders": 0}
    walk(args.folder, fixture, [], files, counters, args.max_items)

    videos = []
    for rel_path, f in files:
        ext = re.search(r"\.([A-Za-z0-9]+)$", f.get("name", ""))
        if ext and ext.group(1).lower() in {"mp4", "mkv", "avi", "mov", "ts", "m2ts", "webm", "rmvb", "wmv", "flv"}:
            videos.append(video_record(rel_path, f))

    if args.filter == "4k":
        videos = [v for v in videos if v["is_4k"]]
    elif args.filter == "hd":
        videos = [v for v in videos if v["long_edge"] >= 1280]
    elif args.filter == "short":
        videos = [v for v in videos if 0 < v["duration"] <= 3600]

    total_size = sum(v["size"] for v in videos)
    now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")

    if args.format == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["name", "duration", "resolution", "codec", "size_bytes", "size_h", "path", "modified"])
        for v in sorted(videos, key=lambda x: x["size"], reverse=True):
            writer.writerow([v["name"], hms(v["duration"]), v["resolution"], v["codec"],
                             v["size"], human_size(v["size"]), v["path"], v["modified"]])
        text = buf.getvalue()
    else:
        lines = [
            f"# 媒体库索引（{now}）",
            "",
            f"共 {len(videos)} 个视频，总量 {human_size(total_size)}"
            + (f"（筛选：{args.filter}）" if args.filter != "all" else ""),
            "",
            "| # | 名称 | 时长 | 分辨率 | 大小 | 编码 | 路径 |",
            "|---|------|------|--------|------|------|------|",
        ]
        for i, v in enumerate(sorted(videos, key=lambda x: x["size"], reverse=True), 1):
            name = v["name"].replace("|", "\\|")
            lines.append(f"| {i} | {name} | {hms(v['duration'])} | {v['resolution'] or '-'} | "
                         f"{human_size(v['size'])} | {v['codec'] or '-'} | {v['path'] or '/'} |")
        pending = [v for v in videos if v["pending"]]
        if pending:
            lines += ["", f"注：{len(pending)} 个视频尚未完成服务端处理（时长未知），已包含在列表中。"]
        text = "\n".join(lines) + "\n"

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"索引已写入：{args.output}（{len(videos)} 个视频）")
    else:
        print(text)


if __name__ == "__main__":
    main()
