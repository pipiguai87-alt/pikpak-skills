#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PikPak 磁力管道（分步执行器）：磁力 → 云下载 → 拉回本地 → 清理。

一条流水线的单步命令；每一步都单独可跑、可检查。
下载/清理环节全部走「先确认后执行」。
本脚本自身不调用删除命令——清理步骤输出建议命令交由调用方执行。

用法：
  # 步骤 1：提交磁力到云下载
  python3 magnet_pipeline.py step1-add "magnet:?xt=..." --folder <folderID>
  # 步骤 2：查任务状态，拿 file_id
  python3 magnet_pipeline.py step2-status <taskID>
  # 步骤 3：下载到本地
  python3 magnet_pipeline.py step3-download <fileID> --dest ./downloads
  # 步骤 4：清理（生成建议命令，不执行）
  python3 magnet_pipeline.py step4-cleanup <fileID> [fileID...]

环境变量：
  PIKPAK_BIN      pikpak 可执行文件路径（默认 "pikpak"）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import NoReturn


def die(msg: str, code: int = 1) -> NoReturn:
    print(f"错误：{msg}", file=sys.stderr)
    raise SystemExit(code)


def pikpak(*args: str, timeout: int = 300, fmt_json: bool = False):
    bin_path = os.environ.get("PIKPAK_BIN", "pikpak")
    cmd = [bin_path, *args]
    if fmt_json:
        cmd += ["-F", "json"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        die(f"找不到 pikpak 命令（{bin_path}）。请先安装 PikPak CLI（官方安装页：https://mypikpak.com/cli）。")
    except subprocess.TimeoutExpired:
        die("pikpak 命令超时。")


def pikpak_json(*args: str, timeout: int = 300):
    rc, out, err = pikpak(*args, timeout=timeout, fmt_json=True)
    if rc != 0:
        return None, (err or out).strip()[:400]
    try:
        return json.loads(out), None
    except json.JSONDecodeError:
        return None, "无法解析 pikpak 输出"


def check_login():
    data, err = pikpak_json("auth", "status", timeout=60)
    if err or not data.get("logged_in"):
        die("未登录。请先运行 `pikpak auth login`。")


def step1_add(args):
    """提交磁力到云下载，返回 task_id。"""
    cmd = ["task", "add", args.magnet]
    if args.folder:
        cmd += ["-p", args.folder]
    if args.name:
        pass  # 官方 add 不支持改名，保留参数说明
    data, err = pikpak_json(*cmd, timeout=180)
    if err:
        die(f"提交失败：{err}")
    task_id = data.get("task_id") or (data.get("task") or {}).get("id", "")
    file_name = data.get("file_name", "")
    print(json.dumps({
        "step": "add",
        "status": "submitted",
        "task_id": task_id,
        "file_name": file_name,
        "next": f"python3 magnet_pipeline.py step2-status {task_id}",
    }, ensure_ascii=False, indent=2))


def step2_status(args):
    """查任务状态。"""
    data, err = pikpak_json("task", "get", args.task_id, timeout=120)
    if err:
        die(f"查询失败：{err}")
    status = (data.get("status") or "").lower()
    result = {
        "step": "status",
        "task_id": args.task_id,
        "status": status,
        "progress": data.get("progress", ""),
        "file_name": data.get("file_name", ""),
        "file_id": data.get("file_id", ""),
        "file_size": data.get("file_size", ""),
    }
    if status == "complete":
        result["next"] = f"python3 magnet_pipeline.py step3-download {data.get('file_id','')} --dest ./downloads"
    elif status == "error":
        result["next"] = "任务失败。查看 message 字段后决定是否重试：pikpak task rm <taskID> 后重新 step1-add"
        result["message"] = data.get("message", "")
    else:
        result["next"] = f"任务进行中，稍后再查：python3 magnet_pipeline.py step2-status {args.task_id}"
    print(json.dumps(result, ensure_ascii=False, indent=2))


def step3_download(args):
    """下载到本地。"""
    dest = Path(args.dest).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    print(f"下载 {args.file_id} 到 {dest} ...")
    # pikpak download -o 只支持单文件输出路径；先尝试用 -o 指定完整目标
    rc, out, err = pikpak("download", args.file_id, "-o", str(dest), timeout=7200)
    if rc != 0:
        # 二进制不支持 -o 指向目录时，退回在当前目录下载
        rc, out, err = pikpak("download", args.file_id, timeout=7200)
    if rc != 0:
        die(f"下载失败：{(err or out).strip()[:300]}")
    print(json.dumps({
        "step": "download",
        "status": "done",
        "file_id": args.file_id,
        "dest": str(dest),
        "note": "若目录下已有同名文件，CLI 可能跳过或覆盖——以实际文件为准",
        "next": f"python3 magnet_pipeline.py step4-cleanup {args.file_id}",
    }, ensure_ascii=False, indent=2))


def step4_cleanup(args):
    """生成清理建议（不执行删除）。"""
    print("以下命令会把文件移入回收站（可恢复）。请确认后手动执行：")
    print()
    for fid in args.file_ids:
        print(f"pikpak rm {fid}")
    print()
    print("提示：官方 CLI 的 rm 只是移入回收站；如需一并删除任务记录（任务ID需从 step1/step2 输出获取）：")
    print("  pikpak task rm <taskID>")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("step1-add", help="提交磁力到云下载")
    p1.add_argument("magnet", help="磁力链接或 HTTP 链接")
    p1.add_argument("--folder", default="", help="目标文件夹 ID（默认根目录）")
    p1.add_argument("--name", default="", help="（预留）自定义名称")
    p1.set_defaults(func=step1_add)

    p2 = sub.add_parser("step2-status", help="查询任务状态")
    p2.add_argument("task_id")
    p2.set_defaults(func=step2_status)

    p3 = sub.add_parser("step3-download", help="从云端下载到本地")
    p3.add_argument("file_id")
    p3.add_argument("--dest", default="./downloads", help="本地目标目录（默认 ./downloads）")
    p3.set_defaults(func=step3_download)

    p4 = sub.add_parser("step4-cleanup", help="生成清理建议命令（不执行）")
    p4.add_argument("file_ids", nargs="+")
    p4.set_defaults(func=step4_cleanup)

    args = ap.parse_args()
    if args.cmd != "step4-cleanup":
        check_login()
    args.func(args)


if __name__ == "__main__":
    main()
