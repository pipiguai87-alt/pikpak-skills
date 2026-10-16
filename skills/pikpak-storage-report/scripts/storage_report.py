#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PikPak 空间体检：目录体积排行 + 大文件清单（只读；不执行删除）。

用法：
  python3 storage_report.py --folder "" --depth 2
  python3 storage_report.py --folder "" --top 30
  python3 storage_report.py --folder "" --json report.json
  python3 storage_report.py --from-fixture dev/fixtures/sample_tree.json --folder ""

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
        die(f"找不到 pikpak 命令（{cmd[0]}）。请先安装 PikPak CLI："
            "curl -fsSL https://download.mypikpak.com/cli/install.sh | bash")
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


class Node:
    """目录树节点。"""

    def __init__(self, name: str, fid: str):
        self.name = name
        self.id = fid
        self.size = 0
        self.file_count = 0
        self.children: list["Node"] = []
        self.files: list[dict] = []

    def to_dict(self):
        return {
            "name": self.name,
            "id": self.id,
            "size": self.size,
            "size_h": human_size(self.size),
            "file_count": self.file_count,
            "children": [c.to_dict() for c in self.children],
        }


def walk_tree(folder_id: str, fixture: "dict | None", name: str, counters, max_items: int) -> Node:
    node = Node(name, folder_id)
    token = ""
    while True:
        page = ls_page(folder_id, fixture, token)
        for f in page.get("files", []):
            kind = f.get("kind")
            if kind == "drive#folder":
                counters["folders"] += 1
                if counters["folders"] > 5000:
                    die("目录数量超过安全上限（5000），已中止。")
                child = walk_tree(f.get("id", ""), fixture, f.get("name", ""), counters, max_items)
                node.size += child.size
                node.file_count += child.file_count
                node.children.append(child)
            elif kind == "drive#file":
                counters["files"] += 1
                if counters["files"] > max_items:
                    die(f"文件数量超过安全上限（{max_items}），已中止。")
                size = to_int(f.get("size"))
                node.size += size
                node.file_count += 1
                node.files.append({
                    "id": f.get("id", ""),
                    "name": f.get("name", ""),
                    "size": size,
                    "size_h": human_size(size),
                    "modified": f.get("modified_time", ""),
                })
        token = page.get("next_page_token", "") or ""
        if not token:
            break
    return node


def collect_files(node: Node, rel_path: list[str], out: list[dict]):
    for f in node.files:
        out.append({**f, "path": "/".join(rel_path + [f["name"]])})
    for c in node.children:
        collect_files(c, rel_path + [c.name], out)


def print_tree(node: Node, depth: int, max_depth: int, indent: int = 0, file=None):
    pad = "  " * indent
    print(f"{pad}{human_size(node.size):>10}  {node.name} ({node.file_count} 文件)", file=file)
    if indent < max_depth:
        for c in sorted(node.children, key=lambda n: n.size, reverse=True):
            print_tree(c, depth + 1, max_depth, indent + 1, file=file)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--folder", default="", help="要分析的目录 ID（默认根目录）")
    ap.add_argument("--from-fixture", help="离线模式：从 JSON fixture 读取（测试用）")
    ap.add_argument("--depth", type=int, default=2, help="目录树展示深度（默认 2）")
    ap.add_argument("--top", type=int, default=20, help="大文件榜数量（默认 20；0=不显示）")
    ap.add_argument("--json", dest="json_out", help='写入完整 JSON 报告（"-" 输出到 stdout）')
    ap.add_argument("--max-items", type=int, default=50000, help="文件数量安全上限（默认 50000）")
    args = ap.parse_args()

    fixture = None
    if args.from_fixture:
        with open(args.from_fixture, encoding="utf-8") as fh:
            fixture = json.load(fh)

    counters = {"files": 0, "folders": 0}
    root = walk_tree(args.folder, fixture, "根目录", counters, args.max_items)

    # --json - 时人类可读信息全部走 stderr，stdout 只输出纯 JSON
    info = sys.stderr if args.json_out == "-" else sys.stdout

    def say(*a, **k):
        print(*a, file=info, **k)

    say(f"扫描完成：目录 {counters['folders']} 个，文件 {counters['files']} 个"
        + ("（离线 fixture 模式）" if fixture is not None else ""))
    say()
    say(f"总量：{human_size(root.size)}，共 {root.file_count} 个文件")
    say()
    say(f"目录体积排行（前 {args.depth} 层）：")
    for c in sorted(root.children, key=lambda n: n.size, reverse=True):
        print_tree(c, 1, args.depth, 1, file=info)
    say()

    if args.top > 0:
        all_files = []
        collect_files(root, [], all_files)
        all_files.sort(key=lambda f: f["size"], reverse=True)
        say(f"大文件 TOP {args.top}：")
        for f in all_files[: args.top]:
            say(f"  {f['size_h']:>10}  {f['modified'][:16]}  {f['path']}")
        say()

    if args.json_out:
        report = {
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "folder": args.folder,
            "source": "fixture" if fixture is not None else "live",
            "total_size": root.size,
            "total_size_h": human_size(root.size),
            "total_files": root.file_count,
            "tree": root.to_dict(),
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
