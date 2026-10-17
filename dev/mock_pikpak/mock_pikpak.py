#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模拟 pikpak CLI：给端到端测试提供一个假的 `pikpak` 命令。

行为：
  pikpak ls --raw -l N [-p FOLDER] [-t TOKEN]   → 输出 fixtures/sample_tree.json 中对应页
  pikpak version                                  → 打印版本

用来验证脚本的完整 subprocess 调用路径（不接触真实账号）。
"""

import json
import sys
from pathlib import Path

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "sample_tree.json"


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "version":
        print("pikpak version v0.0.0-mock")
        return 0
    if not args or args[0] != "ls":
        print(f"mock pikpak: 不支持的命令 {args}", file=sys.stderr)
        return 2

    folder = ""
    token = ""
    i = 1
    while i < len(args):
        if args[i] in ("-p", "--parent") and i + 1 < len(args):
            folder = args[i + 1]
            i += 2
        elif args[i] in ("-t", "--token") and i + 1 < len(args):
            token = args[i + 1]
            i += 2
        elif args[i] in ("-l", "--limit") and i + 1 < len(args):
            i += 2
        elif args[i] == "--raw":
            i += 1
        else:
            print(f"mock pikpak: 不支持或不完整的参数 {args[i]!r}", file=sys.stderr)
            return 2

    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if folder not in data or token not in data[folder]:
        print("mock pikpak: fixture 中没有该目录/分页，不伪造空页成功", file=sys.stderr)
        return 2
    page = data[folder][token]
    print(json.dumps(page, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
