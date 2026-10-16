#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PikPak 分享审计（只读）：列出分享链接，标记已过期/临期/零访问项。

用法：
  python3 share_audit.py
  python3 share_audit.py --json report.json
  python3 share_audit.py --from-fixture dev/fixtures/shares.json

只读：仅调用 `pikpak share ls`；不创建、不删除任何分享。
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


def fetch_shares(fixture):
    """返回全部分享列表（自动翻页）。"""
    if fixture is not None:
        return fixture.get("shares", [])

    shares = []
    token = ""
    for _ in range(20):  # 最多 20 页安全上限
        cmd = [os.environ.get("PIKPAK_BIN", "pikpak"), "share", "ls", "-F", "json", "-l", "100"]
        if token:
            cmd += ["--token", token]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        except FileNotFoundError:
            die("找不到 pikpak 命令（官方安装页：https://mypikpak.com/cli）。")
        except subprocess.TimeoutExpired:
            die("pikpak share ls 超时。")
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout).strip()
            hint = " 请先运行 `pikpak auth login` 登录。" if "UNAUTHENTICATED" in err.upper() else ""
            die(f"pikpak share ls 调用失败：{err[:300]}{hint}")
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            die("无法解析 pikpak share ls 输出。")
        shares.extend(data.get("shares", []))
        token = data.get("next_page_token", "") or ""
        if not token:
            break
    return shares


def parse_time(value) -> datetime | None:
    """尽力解析各种时间表示；无法解析返回 None。"""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        num = float(value)
        if num > 1e12:  # 毫秒时间戳
            num /= 1000.0
        if num > 1e9:  # 秒时间戳
            try:
                return datetime.fromtimestamp(num, tz=timezone.utc)
            except (OverflowError, OSError):
                return None
        return None  # 小数字含义不明，按未知处理
    text = str(value).strip()
    # ISO 或 "2026-01-01 12:00:00" 等形式
    for fmt in (None, "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            if fmt is None:
                dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            else:
                dt = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def audit(shares) -> list[dict]:
    now = datetime.now(timezone.utc)
    result = []
    for s in shares:
        exp_raw = s.get("expiration")
        exp = parse_time(exp_raw)
        created = parse_time(s.get("created"))
        views = s.get("views")
        flags = []
        if exp_raw in (None, "", -1, "-1"):
            flags.append("无过期时间（长期有效）")
        elif exp is None:
            flags.append(f"过期时间无法解析（原始值: {exp_raw!r}）")
        elif exp < now:
            flags.append("已过期")
        elif (exp - now).days <= 7:
            flags.append("7 天内将过期")
        if isinstance(views, int) and views == 0 and created and (now - created).days >= 30:
            flags.append("30 天以上零访问")
        result.append({
            "share_id": s.get("share_id", ""),
            "title": s.get("title", ""),
            "status": s.get("status", ""),
            "views": views,
            "expiration_raw": exp_raw,
            "expiration_parsed": exp.isoformat(timespec="seconds") if exp else None,
            "created": s.get("created", ""),
            "files": s.get("files"),
            "flags": flags,
        })
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from-fixture", help="离线模式（测试用）")
    ap.add_argument("--json", dest="json_out", help='写入 JSON 报告（"-" 到 stdout，人类可读转 stderr）')
    args = ap.parse_args()

    fixture = None
    if args.from_fixture:
        with open(args.from_fixture, encoding="utf-8") as fh:
            fixture = json.load(fh)

    shares = fetch_shares(fixture)
    report = audit(shares)

    out = sys.stderr if args.json_out == "-" else sys.stdout

    def say(*a):
        print(*a, file=out)

    say(f"共 {len(report)} 个分享：")
    say()
    for r in report:
        flags = "；".join(r["flags"]) if r["flags"] else "正常"
        say(f"• {r['title'] or '(无标题)'}  [views={r['views']}]")
        say(f"  {r['share_id']}  → {flags}")
    problematic = [r for r in report if any(f.startswith(("已过期", "7 天内")) for f in r["flags"])]
    if problematic:
        say()
        say("建议操作（请自行确认后执行；删除分享命令：）")
        for r in problematic:
            say(f"  pikpak share rm {r['share_id']}   # {r['title']}（{'；'.join(r['flags'])}）")
    say()
    say("提示：本工具只读；删除分享需你确认后自行执行上面的命令。")

    if args.json_out:
        text = json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                           "count": len(report), "shares": report}, ensure_ascii=False, indent=2)
        if args.json_out == "-":
            print(text)
        else:
            with open(args.json_out, "w", encoding="utf-8") as fh:
                fh.write(text)
            print(f"报告已写入：{args.json_out}")


if __name__ == "__main__":
    main()
