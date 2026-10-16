#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PikPak WebDAV 准备检查：会员状态 + 配额用量（只读）。

用途：在引导用户开通/配置 WebDAV 前，先确认可行性：
  1. 是否登录
  2. 是否会员（WebDAV 是会员功能）
  3. 已连接应用配额用量（WebDAV 会共享这个配额）

用法：
  python3 webdav_check.py
  python3 webdav_check.py --json

环境变量：
  PIKPAK_BIN      pikpak 可执行文件路径（默认 "pikpak"）
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from typing import NoReturn


def die(msg: str, code: int = 1) -> NoReturn:
    print(f"错误：{msg}", file=sys.stderr)
    raise SystemExit(code)


def pikpak_json(*args: str, timeout: int = 60):
    bin_path = os.environ.get("PIKPAK_BIN", "pikpak")
    try:
        proc = subprocess.run([bin_path, *args, "-F", "json"],
                              capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return None, f"找不到 pikpak 命令（官方安装页：https://mypikpak.com/cli）"
    except subprocess.TimeoutExpired:
        return None, "pikpak 命令超时"
    if proc.returncode != 0:
        return None, (proc.stderr or proc.stdout).strip()[:300]
    try:
        return json.loads(proc.stdout), None
    except json.JSONDecodeError:
        return None, "无法解析 pikpak 输出"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", dest="json_out", action="store_true", help="JSON 输出")
    args = ap.parse_args()

    status, err = pikpak_json("auth", "status")
    if err or status is None:
        die(f"获取账号状态失败：{err}")

    logged_in = bool(status.get("logged_in"))
    result = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "logged_in": logged_in,
        "can_use_webdav": False,
        "checks": [],
    }

    if not logged_in:
        result["checks"].append("未登录：先运行 `pikpak auth login`")
        emit(result, args.json_out)
        return

    vip = status.get("vip") or {}
    vip_type = vip.get("type") or "Free"
    vip_status = vip.get("status") or ""
    expire = vip.get("expire_time") or ""
    is_member = vip_type.lower() not in ("", "free", "none")

    result["vip"] = {"type": vip_type, "status": vip_status, "expire_time": expire}
    result["can_use_webdav"] = is_member

    if is_member:
        result["checks"].append(f"✓ 会员状态有效（{vip_type}），可使用 WebDAV")
        if expire:
            result["checks"].append(f"  到期时间：{expire}")
    else:
        result["checks"].append("✗ 当前为非会员：WebDAV 是会员专属功能，需要开通会员后使用")

    storage = status.get("storage") or {}
    result["storage"] = {
        "total": storage.get("total"),
        "used": storage.get("used"),
        "in_trash": storage.get("in_trash"),
    }
    trash = storage.get("in_trash") or ""
    if trash and trash != "0 B":
        result["checks"].append(f"⚠ 回收站占用 {trash}（回收站也占空间；二次删除才释放）")

    result["checks"].append("提示：WebDAV 属于「已连接的应用」，与 CLI/MCP 共享月度配额的 25%（触顶降速至约 100 KB/s）")
    result["checks"].append("配置入口：PikPak 网页版 → 设置 → 访问与集成 → WebDAV")

    emit(result, args.json_out)


def emit(result: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print("═══ PikPak WebDAV 准备检查 ═══")
    print(f"登录状态：{'已登录' if result['logged_in'] else '未登录'}")
    print(f"可用 WebDAV：{'是' if result.get('can_use_webdav') else '否'}")
    print()
    for c in result.get("checks", []):
        print(f"  {c}")


if __name__ == "__main__":
    main()
