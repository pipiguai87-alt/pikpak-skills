#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PikPak 任务守护：巡检云下载任务，失败重试（受限），完成通知。

用法：
  python3 task_guardian.py --once                 # 单次巡检
  python3 task_guardian.py --interval 300         # 常驻（每 300 秒）
  python3 task_guardian.py --once --from-fixture f.json   # 离线测试

状态文件：~/.pikpak/guardian_state.json
源登记文件（可选）：~/.pikpak/watch_sources.json

安全设计：
  - 只调用 task ls / task get / task add / task rm（重试清理）与 file 查询命令
  - 不删除文件、不修改用户资源、不主动订阅任何内容
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import NoReturn

STATE_PATH = Path.home() / ".pikpak" / "guardian_state.json"
WATCH_PATH = Path.home() / ".pikpak" / "watch_sources.json"
MAX_RETRIES = 3


def die(msg: str, code: int = 1) -> NoReturn:
    print(f"错误：{msg}", file=sys.stderr)
    raise SystemExit(code)


def log(msg: str) -> None:
    print(time.strftime("[%Y-%m-%d %H:%M:%S] ") + msg, flush=True)


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def pikpak_json(*args: str, timeout: int = 120):
    bin_path = os.environ.get("PIKPAK_BIN", "pikpak")
    try:
        proc = subprocess.run([bin_path, *args, "-F", "json"],
                              capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return None, "找不到 pikpak 命令"
    except subprocess.TimeoutExpired:
        return None, "pikpak 命令超时"
    if proc.returncode != 0:
        return None, (proc.stderr or proc.stdout).strip()[:300]
    try:
        return json.loads(proc.stdout), None
    except json.JSONDecodeError:
        return None, "无法解析 pikpak 输出"


def notify(title: str, message: str) -> None:
    """macOS 通知；其他平台降级为日志。"""
    if sys.platform == "darwin":
        try:
            script = f'display notification "{message}" with title "{title}"'
            subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)
            return
        except Exception:  # noqa: BLE001
            pass
    log(f"[通知] {title}: {message}")


def fetch_tasks(fixture):
    """返回任务列表。fixture 非 None 时离线读取。"""
    if fixture is not None:
        return fixture.get("tasks", []), None
    data, err = pikpak_json("task", "ls", "-l", "100")
    if err:
        return None, err
    return data.get("tasks", []), None


def retry_task(task: dict, sources: dict, state: dict) -> bool:
    """尝试重试失败任务。无源可查时返回 False。"""
    tid = task.get("id", "")
    retries = state.get("retries", {}).get(tid, 0)
    if retries >= MAX_RETRIES:
        return False

    # 从 watch_sources 中按名称模糊匹配源（保守：仅匹配已登记源）
    name = (task.get("name") or "").lower()
    url = None
    for entry in sources:
        src = entry.get("url", "")
        note = (entry.get("note") or "").lower()
        if src and (note and (note in name or name in note)):
            url = src
            break
    if not url:
        return False

    bin_path = os.environ.get("PIKPAK_BIN", "pikpak")
    try:
        subprocess.run([bin_path, "task", "rm", tid], capture_output=True, text=True, timeout=60)
        proc = subprocess.run([bin_path, "task", "add", url], capture_output=True, text=True, timeout=180)
        ok = proc.returncode == 0
    except Exception:  # noqa: BLE001
        ok = False

    if ok:
        state.setdefault("retries", {})[tid] = retries + 1
        log(f"已重试任务 {tid}（第 {retries + 1} 次）")
    return ok


def once(fixture=None) -> dict:
    state = load_json(STATE_PATH, {})
    sources = load_json(WATCH_PATH, [])
    tasks, err = fetch_tasks(fixture)
    if err:
        log(f"任务列表获取失败：{err}")
        return state

    running = [t for t in tasks if (t.get("status") or "").lower() in ("running", "pending")]
    failed = [t for t in tasks if (t.get("status") or "").lower() == "error"]
    complete = [t for t in tasks if (t.get("status") or "").lower() == "complete"]

    # 新完成任务 → 通知
    seen_done = set(state.get("seen_done", []))
    new_done = [t for t in complete if t.get("id") not in seen_done]
    for t in new_done:
        notify("PikPak 转存完成", f"{(t.get('name') or '?')[:60]} 已入库")
        seen_done.add(t.get("id", ""))
    state["seen_done"] = list(seen_done)[-500:]

    # 失败任务 → 尝试重试（有源时）
    for t in failed:
        if retry_task(t, sources, state):
            continue
        # 只对新失败通知一次
        seen_failed = set(state.get("seen_failed", []))
        if t.get("id") not in seen_failed:
            notify("PikPak 任务失败", f"{(t.get('name') or '?')[:50]}（无源可自动重试）")
            seen_failed.add(t.get("id", ""))
        state["seen_failed"] = list(seen_failed)[-500:]

    state["last_run"] = datetime.now().astimezone().isoformat(timespec="seconds")
    save_json(STATE_PATH, state)

    log(f"巡检完成：进行中 {len(running)}、失败 {len(failed)}、完成 {len(complete)}"
        + (f"（新完成 {len(new_done)}）" if new_done else ""))
    return state


def daily_report(state: dict) -> str:
    done = state.get("seen_done", [])
    return f"PikPak 任务日报：累计记录完成 {len(done)} 个任务。"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true", help="单次巡检后退出")
    ap.add_argument("--interval", type=int, default=300, help="常驻巡检间隔秒数（默认 300）")
    ap.add_argument("--from-fixture", help="离线模式（测试用，配合 --once）")
    args = ap.parse_args()

    fixture = None
    if args.from_fixture:
        with open(args.from_fixture, encoding="utf-8") as fh:
            fixture = json.load(fh)

    if args.once or fixture is not None:
        once(fixture)
        return

    log(f"守护启动，间隔 {args.interval}s。Ctrl+C 退出。")
    while True:
        try:
            once()
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # noqa: BLE001
            log(f"巡检异常: {exc}")
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已退出。")
