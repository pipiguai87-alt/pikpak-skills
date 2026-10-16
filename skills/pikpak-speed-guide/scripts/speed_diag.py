#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PikPak 速度问题诊断（只读）：网络连通性 + 延迟测试 + 方案建议。

不下载、不修改、不接触用户凭据；只做 HTTP 连通性和延迟探测。

用法：
  python3 speed_diag.py
  python3 speed_diag.py --json
"""

from __future__ import annotations

import argparse
import json
import socket
import ssl
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime


TARGETS = [
    ("主站 mypikpak.com", "https://mypikpak.com"),
    ("WebDAV 入口 dav.mypikpak.com", "https://dav.mypikpak.com"),
    ("API api-drive.mypikpak.com", "https://api-drive.mypikpak.com"),
]


def probe(url: str, timeout: float = 8.0):
    """探测一个 URL：DNS 解析时间、TCP+TLS 时间、HTTP 状态。"""
    host = url.replace("https://", "").split("/")[0]
    result: dict = {"url": url, "host": host}

    # DNS 解析
    t0 = time.perf_counter()
    try:
        addr = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
        result["dns_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        result["ip"] = addr[0][4][0]
    except socket.gaierror as exc:
        result["dns_error"] = str(exc)[:100]
        result["ok"] = False
        return result

    # HTTP HEAD 请求（测量完整握手+响应）
    t1 = time.perf_counter()
    try:
        req = urllib.request.Request(url, method="HEAD",
                                     headers={"User-Agent": "Mozilla/5.0 (speed-diag)"})
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            result["http_status"] = resp.status
    except urllib.error.HTTPError as exc:
        result["http_status"] = exc.code  # 4xx/5xx 也算连通
    except Exception as exc:  # noqa: BLE001
        result["http_error"] = str(exc)[:100]
        result["ok"] = False
        result["total_ms"] = round((time.perf_counter() - t1) * 1000, 1)
        return result

    result["total_ms"] = round((time.perf_counter() - t1) * 1000, 1)
    result["ok"] = True
    return result


def recommend(results: list[dict], vip_info: str = "") -> list[str]:
    tips = []
    ok_count = sum(1 for r in results if r.get("ok"))
    slow = [r for r in results if r.get("ok") and r.get("total_ms", 0) > 1500]

    if ok_count == 0:
        tips.append("✗ 所有 PikPak 域名不可达：先检查网络/代理，或换一个网络环境重试")
    elif ok_count < len(results):
        tips.append("⚠ 部分域名不可达：可能是运营商对该域名的专项干扰（换网络对比可确认）")
    if slow:
        names = "、".join(r["host"] for r in slow)
        tips.append(f"⚠ 以下域名响应较慢（>1.5s）：{names} —— 典型的运营商 QoS 特征")

    if ok_count > 0:
        tips.append("→ 建议按顺序尝试：① 换网络（移动→电信/联通热点）对比 ② 换 DNS（223.5.5.5）③ 用代理/VPN")
        tips.append("→ 如果网页下载被限速在 2MB/s：WebDAV 通道（会员）常能绕开，见 pikpak-webdav-guide")
        tips.append("→ 追求极致速度：部署 pikpak-webdav-proxy（开源，实测 10-30MB/s），见本 skill 方案 3")

    if vip_info:
        tips.append(f"账号：{vip_info}")
    return tips


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", dest="json_out", action="store_true")
    args = ap.parse_args()

    results = [probe(url) for _, url in TARGETS]
    tips = recommend(results)

    report = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "results": results,
        "recommendations": tips,
    }

    if args.json_out:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    print("═══ PikPak 网络诊断 ═══")
    print()
    for name, r in zip([n for n, _ in TARGETS], results):
        if r.get("ok"):
            print(f"  ✓ {name}")
            print(f"      DNS {r.get('dns_ms')}ms | 总响应 {r.get('total_ms')}ms | IP {r.get('ip')}")
        else:
            err = r.get("dns_error") or r.get("http_error") or "未知错误"
            print(f"  ✗ {name} — {err}")
    print()
    print("诊断建议：")
    for t in tips:
        print(f"  {t}")


if __name__ == "__main__":
    main()
