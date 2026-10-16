#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PikPak 批量备份：把云盘文件夹树拉回本地（断点续传 + 清单）。

用法：
  python3 batch_backup.py --folder "<folderID>" --dest /Volumes/Backup/pikpak
  python3 batch_backup.py --folder "<folderID>" --dest ./backup --dry-run
  python3 batch_backup.py --from-fixture dev/fixtures/sample_tree.json --folder "" --dest /tmp/bk --dry-run

安全设计：
  - 只调用 `pikpak ls`（读）与 `pikpak download ... -o`（写本地）；不删除云端或本地任何文件。
  - --dry-run 只列出计划，不触网下载。

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
from pathlib import Path
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


def sanitize(name: str) -> str:
    """把云盘文件名清洗为安全的本机文件名（防路径穿越）。"""
    name = name.replace("/", "_").replace("\\", "_").replace("\x00", "")
    name = re.sub(r"^\.+", "_", name)  # 防 ../ 与隐藏文件歧义
    return name or "_"


def pikpak(*args: str, timeout: int = 3600):
    bin_path = os.environ.get("PIKPAK_BIN", "pikpak")
    try:
        proc = subprocess.run([bin_path, *args], capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", "找不到 pikpak 命令"
    except subprocess.TimeoutExpired:
        return 124, "", "pikpak 命令超时"


def ls_page(folder_id: str, fixture: "dict | None", page_token: str = "") -> dict:
    if fixture is not None:
        node = fixture.get(folder_id) or {}
        return dict(node.get(page_token) or {"files": [], "next_page_token": ""})

    rc, out, err = pikpak("ls", "--raw", "-l", "500", *(["-p", folder_id] if folder_id else []),
                          *(["-t", page_token] if page_token else []))
    if rc != 0:
        hint = " 请先运行 `pikpak auth login` 登录。" if "UNAUTHENTICATED" in (err or "").upper() else ""
        die(f"pikpak ls 调用失败：{(err or out).strip()[:300]}{hint}")
    try:
        return json.loads(out)
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
                walk(f.get("id", ""), fixture, rel_path + [sanitize(f.get("name", ""))], out, counters, max_items)
            elif kind == "drive#file":
                counters["files"] += 1
                if counters["files"] > max_items:
                    die(f"文件数超过安全上限（{max_items}）。")
                out.append((rel_path, f))
        token = page.get("next_page_token", "") or ""
        if not token:
            break


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--folder", default="", help="要备份的目录 ID（默认根目录）")
    ap.add_argument("--dest", required=True, help="本地目标目录")
    ap.add_argument("--from-fixture", help="离线模式（测试用，配合 --dry-run）")
    ap.add_argument("--dry-run", action="store_true", help="只列出计划，不实际下载")
    ap.add_argument("--max-items", type=int, default=50000, help="文件数安全上限")
    args = ap.parse_args()

    fixture = None
    if args.from_fixture:
        with open(args.from_fixture, encoding="utf-8") as fh:
            fixture = json.load(fh)

    dest = Path(args.dest).expanduser()
    if not args.dry_run and not dest.exists():
        die(f"目标目录不存在：{dest}（先创建好再跑）")

    files = []
    counters = {"files": 0, "folders": 0}
    walk(args.folder, fixture, [], files, counters, args.max_items)
    total = sum(to_int(f.get("size")) for _, f in files)

    print(f"计划备份：{counters['folders']} 个目录、{len(files)} 个文件、{human_size(total)}")
    print(f"目标目录：{dest}")

    if args.dry_run:
        print()
        for rel, f in files[:50]:
            print(f"  {human_size(to_int(f.get('size'))):>10}  {'/'.join(rel + [f.get('name','')])}")
        if len(files) > 50:
            print(f"  … 还有 {len(files) - 50} 个文件")
        print("\n（--dry-run：未实际下载）")
        return

    dest.mkdir(parents=True, exist_ok=True)
    manifest = {
        "created": datetime.now().astimezone().isoformat(timespec="seconds"),
        "folder": args.folder,
        "dest": str(dest),
        "files": [],
    }
    ok = skipped = failed = 0

    for rel, f in files:
        name = sanitize(f.get("name", ""))
        target_dir = dest.joinpath(*rel)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / name
        size = to_int(f.get("size"))

        entry = {"id": f.get("id", ""), "path": str(Path(*rel) / name), "size": size, "status": ""}

        if target.exists() and target.stat().st_size == size:
            skipped += 1
            entry["status"] = "skipped"
            print(f"  跳过（已存在）：{entry['path']}")
            manifest["files"].append(entry)
            continue

        print(f"  下载：{entry['path']} （{human_size(size)}）")
        rc, out, err = pikpak("download", f.get("id", ""), "-o", str(target))
        if rc == 0 and target.exists():
            ok += 1
            entry["status"] = "ok"
        else:
            failed += 1
            entry["status"] = "failed"
            entry["error"] = (err or out).strip()[:200]
            print(f"    失败：{entry['error']}")
        manifest["files"].append(entry)

    manifest_path = dest / "backup_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print(f"完成：成功 {ok}、跳过 {skipped}、失败 {failed}（共 {len(files)}）")
    print(f"清单：{manifest_path}")
    if failed:
        print("失败项已记录在清单中；重跑同命令可续传。")


if __name__ == "__main__":
    main()
