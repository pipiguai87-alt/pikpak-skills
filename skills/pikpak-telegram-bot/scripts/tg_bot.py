#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PikPak Telegram Bot 遥控器。

在 Telegram 里发磁力/链接，本机自动转存到 PikPak 云盘。
纯标准库实现（urllib 长轮询 Telegram Bot API + subprocess 调 pikpak CLI）。

安全设计：
  - 白名单强制：PIKPAK_ALLOWED_CHAT_IDS 未设置或为空时，拒绝一切消息。
  - 只实现「转存 + 查询」；不提供删除/清空等危险操作。
  - token 通过环境变量传入，不写日志、不回显。

用法：
  export TG_BOT_TOKEN="123:ABC..."
  export PIKPAK_ALLOWED_CHAT_IDS="12345678"   # 逗号分隔
  python3 tg_bot.py                            # 前台运行
  python3 tg_bot.py --check                    # 列出最近跟 bot 说过话的 chat id
  python3 tg_bot.py --once                     # 处理一轮消息后退出（测试用）

环境变量：
  TG_BOT_TOKEN              Telegram bot token（必需）
  PIKPAK_ALLOWED_CHAT_IDS   允许的 chat id，逗号分隔（必需，空=拒绝所有人）
  PIKPAK_BIN                pikpak 可执行路径（默认 "pikpak"）
  TG_API_BASE               Telegram API 基址（默认 https://api.telegram.org）
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import NoReturn

POLL_TIMEOUT = 30
MAGNET_RE = re.compile(r"^(magnet:|https?://|thunder://|ed2k://)", re.IGNORECASE)
HELP_TEXT = """PikPak 遥控器指令：
• 直接发链接 → 转存到根目录
• /save <链接> [to <文件夹名>] → 转存（可指定文件夹）
• /tasks → 查看进行中任务
• /status <taskID> → 查询单个任务
• /ls → 列出根目录最近文件
• /help → 本帮助"""


def die(msg: str, code: int = 1) -> NoReturn:
    print(f"错误：{msg}", file=sys.stderr)
    raise SystemExit(code)


def log(msg: str) -> None:
    print(time.strftime("[%Y-%m-%d %H:%M:%S] ") + msg, flush=True)


# ---------------------------------------------------------------------------
# Telegram API
# ---------------------------------------------------------------------------

class Telegram:
    def __init__(self, token: str, base: str = "https://api.telegram.org"):
        self.base = f"{base}/bot{token}"

    def _call(self, method: str, **params):
        url = f"{self.base}/{method}"
        data = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request(url, data=data)
        try:
            with urllib.request.urlopen(req, timeout=POLL_TIMEOUT + 15) as resp:
                payload = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")[:300]
            log(f"Telegram API {method} HTTP {exc.code}: {body}")
            return None
        except (urllib.error.URLError, TimeoutError) as exc:
            log(f"Telegram API {method} 网络错误: {exc}")
            return None
        if not payload.get("ok"):
            log(f"Telegram API {method} 返回错误: {str(payload)[:300]}")
            return None
        return payload.get("result")

    def get_updates(self, offset: int | None = None):
        params = {"timeout": POLL_TIMEOUT, "allowed_updates": '["message"]'}
        if offset is not None:
            params["offset"] = offset
        return self._call("getUpdates", **params)

    def send(self, chat_id, text: str):
        return self._call("sendMessage", chat_id=chat_id, text=text,
                          disable_web_page_preview="true")


# ---------------------------------------------------------------------------
# PikPak CLI 包装
# ---------------------------------------------------------------------------

def pikpak(*args: str, timeout: int = 120):
    """调用 pikpak CLI；返回 (rc, stdout, stderr)。"""
    bin_path = os.environ.get("PIKPAK_BIN", "pikpak")
    try:
        proc = subprocess.run([bin_path, *args], capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", "找不到 pikpak 命令，请先安装 PikPak CLI"
    except subprocess.TimeoutExpired:
        return 124, "", f"pikpak 命令超时（{timeout}s）"


def pikpak_json(*args: str, timeout: int = 120):
    rc, out, err = pikpak(*args, "-F", "json", timeout=timeout)
    if rc != 0:
        return None, (err or out or f"exit {rc}").strip()[:300]
    try:
        return json.loads(out), None
    except json.JSONDecodeError:
        return None, "无法解析 pikpak 输出"


def find_folder_id(name: str):
    """在根目录下按名称找文件夹 ID。"""
    data, err = pikpak_json("ls", "--type", "folder")
    if err:
        return None, err
    for f in data.get("files", []):
        if f.get("name") == name:
            return f.get("id", ""), None
    return None, f"根目录下没找到文件夹「{name}」"


# ---------------------------------------------------------------------------
# 指令处理
# ---------------------------------------------------------------------------

def handle_save(tg: Telegram, chat_id, text: str):
    m = re.match(r"^/save\s+(\S+)(?:\s+to\s+(.+))?$", text, re.IGNORECASE)
    if not m:
        tg.send(chat_id, "用法：/save <链接> [to <文件夹名>]")
        return
    url, folder_name = m.group(1), (m.group(2) or "").strip()

    args = ["task", "add", url]
    if folder_name:
        fid, err = find_folder_id(folder_name)
        if err:
            tg.send(chat_id, f"找不到文件夹：{err}")
            return
        args += ["-p", fid]

    tg.send(chat_id, "已提交转存任务，正在获取任务信息…")
    data, err = pikpak_json(*args, timeout=180)
    if err:
        tg.send(chat_id, f"转存失败：{err}")
        return
    task_id = data.get("task_id") or (data.get("task") or {}).get("id", "")
    file_name = data.get("file_name", "")
    tg.send(chat_id, f"✓ 已开始转存\n文件名：{file_name}\n任务 ID：{task_id}\n\n用 /status {task_id} 查进度")


def handle_tasks(tg: Telegram, chat_id):
    data, err = pikpak_json("task", "ls", "-s", "running,pending")
    if err:
        tg.send(chat_id, f"查询失败：{err}")
        return
    tasks = data.get("tasks", [])
    if not tasks:
        tg.send(chat_id, "当前没有进行中的任务。")
        return
    lines = ["进行中的任务："]
    for t in tasks[:15]:
        lines.append(f"• {t.get('name', '?')[:40]}\n  {t.get('status', '?')} {t.get('progress', '')} ID: {t.get('id', '')}")
    tg.send(chat_id, "\n".join(lines))


def handle_status(tg: Telegram, chat_id, task_id: str):
    data, err = pikpak_json("task", "get", task_id)
    if err:
        tg.send(chat_id, f"查询失败：{err}")
        return
    tg.send(chat_id, f"任务 {task_id}\n状态：{data.get('status', '?')}\n进度：{data.get('progress', '?')}\n文件名：{data.get('file_name', '?')}")


def handle_ls(tg: Telegram, chat_id):
    data, err = pikpak_json("ls", "-l", "10")
    if err:
        tg.send(chat_id, f"查询失败：{err}")
        return
    files = data.get("files", [])
    if not files:
        tg.send(chat_id, "根目录是空的。")
        return
    lines = ["根目录最近文件："]
    for f in files[:10]:
        kind = "📁" if f.get("kind") == "drive#folder" else "📄"
        lines.append(f"{kind} {f.get('name', '?')[:50]}")
    tg.send(chat_id, "\n".join(lines))


def process_message(tg: Telegram, msg: dict, allowed: set):
    chat = msg.get("chat") or {}
    chat_id = str(chat.get("id", ""))
    text = (msg.get("text") or "").strip()
    if not text:
        return

    if chat_id not in allowed:
        log(f"拒绝未授权 chat_id={chat_id} 的消息")
        return  # 静默拒绝，不回复（不给陌生人任何反馈）

    log(f"收到来自 {chat_id} 的消息：{text[:80]}")

    if text.startswith("/help") or text == "/start":
        tg.send(chat_id, HELP_TEXT)
    elif text.startswith("/save"):
        handle_save(tg, chat_id, text)
    elif text.startswith("/tasks"):
        handle_tasks(tg, chat_id)
    elif text.startswith("/status"):
        parts = text.split()
        if len(parts) < 2:
            tg.send(chat_id, "用法：/status <taskID>")
        else:
            handle_status(tg, chat_id, parts[1])
    elif text.startswith("/ls"):
        handle_ls(tg, chat_id)
    elif MAGNET_RE.match(text):
        # 纯链接消息：按默认保存处理
        handle_save(tg, chat_id, f"/save {text}")
    else:
        tg.send(chat_id, "没看懂。发一个磁力/链接，或发送 /help 查看指令。")


# ---------------------------------------------------------------------------
# 主循环
# ---------------------------------------------------------------------------

def parse_allowed(value: str) -> set:
    return {x.strip() for x in value.split(",") if x.strip()}


def check_mode(tg: Telegram):
    """列出最近跟 bot 有过对话的 chat（帮用户拿 chat_id）。"""
    updates = tg.get_updates()
    if updates is None:
        die("获取 updates 失败，请检查 token。")
    seen = {}
    for u in updates:
        msg = u.get("message") or {}
        chat = msg.get("chat") or {}
        if chat.get("id"):
            seen[str(chat["id"])] = chat.get("title") or chat.get("username") or chat.get("first_name") or "?"
    if not seen:
        print("还没有人给 bot 发过消息。请先在 Telegram 里给 bot 发一条消息（比如 /start），再重跑 --check。")
    else:
        print("发现以下 chat：")
        for cid, name in seen.items():
            print(f"  chat_id={cid}  ({name})")
        print("\n把要用的人的 chat_id 填进 PIKPAK_ALLOWED_CHAT_IDS（逗号分隔）。")


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="列出最近与 bot 对话的 chat id 后退出")
    ap.add_argument("--once", action="store_true", help="处理一轮后退出（测试用）")
    args = ap.parse_args()

    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    if not token:
        die("未设置 TG_BOT_TOKEN。请在 BotFather 创建 bot 后：export TG_BOT_TOKEN=\"...\"")

    base = os.environ.get("TG_API_BASE", "https://api.telegram.org")
    tg = Telegram(token, base)

    if args.check:
        check_mode(tg)
        return

    allowed = parse_allowed(os.environ.get("PIKPAK_ALLOWED_CHAT_IDS", ""))
    if not allowed:
        die("未设置 PIKPAK_ALLOWED_CHAT_IDS（白名单为空时拒绝所有消息）。\n"
            "先运行 --check 获取你的 chat_id，再设置后启动。")

    # 先确认 pikpak 可用
    rc, out, err = pikpak("version", timeout=15)
    if rc != 0:
        log(f"警告：pikpak 命令不可用（{err.strip()[:120]}）。消息处理时会报错。")

    log(f"Bot 启动。白名单 {len(allowed)} 个 chat。Ctrl+C 退出。")
    offset = None
    while True:
        updates = tg.get_updates(offset)
        if updates is None:
            time.sleep(3)
            continue
        for u in updates:
            offset = u.get("update_id", 0) + 1
            msg = u.get("message")
            if msg:
                try:
                    process_message(tg, msg, allowed)
                except Exception as exc:  # noqa: BLE001
                    log(f"处理消息异常: {exc}")
        if args.once:
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已退出。")
