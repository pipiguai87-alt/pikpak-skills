#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PikPak 云端文件搜索（只读）：遍历目录树，按关键词匹配文件名与路径。

官方 CLI 的 search 命令因服务端 API 下线而不可用；本脚本用遍历方式补上这个缺口。
支持：多关键词（AND/OR）、正则、类型过滤、按大小/时间排序、结果数量限制。

用法：
  python3 search_files.py --folder "" --query "Sintel"
  python3 search_files.py --folder "" --query "4k 国语" --mode and
  python3 search_files.py --folder "" --query "\\.mkv$" --regex
  python3 search_files.py --folder "" --query "Sintel" --type file --sort size --limit 20
  python3 search_files.py --from-fixture dev/fixtures/sample_tree.json --folder "" --query "Sintel"

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


def parse_time(value: str) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    text = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def ls_page(folder_id: str, fixture: "dict | None", page_token: str = "") -> dict:
    """取一页目录列表。fixture 非 None 时离线读取。"""
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
        die(f"找不到 pikpak 命令（{cmd[0]}）。请先安装 PikPak CLI（官方安装页：https://mypikpak.com/cli）。")
    except subprocess.TimeoutExpired:
        die(f"pikpak ls 超时（{timeout}s），请检查网络后重试。")
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        hint = " 请先运行 `pikpak auth login` 登录。" if "UNAUTHENTICATED" in err.upper() else ""
        die(f"pikpak ls 调用失败：{err[:400]}{hint}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        die("无法解析 pikpak ls 输出为 JSON。")


def walk(folder_id: str, fixture: "dict | None", rel_path, out, counters, max_items: int) -> None:
    """遍历目录树，收集 (rel_path, file_dict)。"""
    token = ""
    while True:
        page = ls_page(folder_id, fixture, token)
        for f in page.get("files", []):
            kind = f.get("kind")
            if kind == "drive#folder":
                counters["folders"] += 1
                if counters["folders"] > 5000:
                    die("目录数量超过安全上限（5000），已中止。请缩小搜索范围。")
                walk(f.get("id", ""), fixture, rel_path + [f.get("name", "")], out, counters, max_items)
            elif kind == "drive#file":
                counters["files"] += 1
                if counters["files"] > max_items:
                    die(f"文件数量超过安全上限（{max_items}），已中止。请缩小搜索范围。")
                out.append((rel_path, f))
        token = page.get("next_page_token", "") or ""
        if not token:
            break


def match(name: str, full_path: str, queries: list[str], mode: str, regex: bool) -> list[str]:
    """返回命中的关键词列表；全不中返回空列表。"""
    hits = []
    for q in queries:
        if regex:
            try:
                if re.search(q, name, re.IGNORECASE) or re.search(q, full_path, re.IGNORECASE):
                    hits.append(q)
            except re.error as exc:
                die(f"正则错误（{q}）：{exc}")
        else:
            ql = q.lower()
            if ql in name.lower() or ql in full_path.lower():
                hits.append(q)
    if mode == "and":
        return hits if len(hits) == len(queries) else []
    return hits  # or 模式：命中任一即可


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--folder", default="", help="搜索起点目录 ID（默认根目录）")
    ap.add_argument("--from-fixture", help="离线模式（测试用）")
    ap.add_argument("--query", required=True, help="搜索词（空格分隔多个；--mode and 时全部需命中）")
    ap.add_argument("--mode", choices=["or", "and"], default="or", help="多词逻辑：or（默认）/ and")
    ap.add_argument("--regex", action="store_true", help="把 query 当作正则表达式")
    ap.add_argument("--type", choices=["file", "folder", "any"], default="any", help="只搜文件/文件夹/不限")
    ap.add_argument("--sort", choices=["name", "size", "time"], default="name", help="排序方式")
    ap.add_argument("--desc", action="store_true", help="降序")
    ap.add_argument("--limit", type=int, default=50, help="结果数量上限（默认 50）")
    ap.add_argument("--json", dest="json_out", help='JSON 输出（"-" 到 stdout，人类可读转 stderr）')
    ap.add_argument("--max-items", type=int, default=50000, help="遍历文件数安全上限")
    args = ap.parse_args()

    fixture = None
    if args.from_fixture:
        with open(args.from_fixture, encoding="utf-8") as fh:
            fixture = json.load(fh)

    queries = args.query.split()
    if not queries:
        die("query 为空。")

    files = []
    counters = {"files": 0, "folders": 0}
    walk(args.folder, fixture, [], files, counters, args.max_items)

    results = []
    for rel_path, f in files:
        name = f.get("name", "")
        full_path = "/".join(rel_path + [name])
        if args.type == "file" and f.get("kind") != "drive#file":
            continue
        hits = match(name, full_path, queries, args.mode, args.regex)
        if hits:
            results.append({
                "id": f.get("id", ""),
                "name": name,
                "path": full_path,
                "size": to_int(f.get("size")),
                "size_h": human_size(to_int(f.get("size"))),
                "modified": f.get("modified_time", ""),
                "hits": hits,
            })

    # 排序
    if args.sort == "size":
        results.sort(key=lambda r: r["size"], reverse=args.desc)
    elif args.sort == "time":
        results.sort(key=lambda r: parse_time(r["modified"]), reverse=args.desc)
    else:
        results.sort(key=lambda r: r["name"].lower(), reverse=args.desc)

    total_found = len(results)
    results = results[: args.limit]

    out = sys.stderr if args.json_out == "-" else sys.stdout

    def say(*a):
        print(*a, file=out)

    say(f"搜索完成：遍历 {counters['folders']} 个目录、{counters['files']} 个文件，"
        f"命中 {total_found} 个（显示前 {len(results)} 个）")
    say()
    for r in results:
        say(f"  {r['size_h']:>10}  {r['modified'][:10]}  {r['path']}")

    if args.json_out:
        report = {
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "query": args.query,
            "mode": args.mode,
            "regex": args.regex,
            "scanned_files": counters["files"],
            "scanned_folders": counters["folders"],
            "total_found": total_found,
            "results": results,
        }
        text = json.dumps(report, ensure_ascii=False, indent=2)
        if args.json_out == "-":
            print(text)
        else:
            with open(args.json_out, "w", encoding="utf-8") as fh:
                fh.write(text)
            print(f"报告已写入：{args.json_out}")


if __name__ == "__main__":
    main()
