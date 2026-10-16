#!/usr/bin/env bash
# 端到端冒烟测试：用模拟 pikpak 命令跑完整主流程（不接触真实账号/网络）。
# 用法：bash dev/smoke_e2e.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MOCK="$ROOT/dev/mock_pikpak"

# 造一个名为 pikpak 的假命令放入临时 PATH
FAKE_BIN="$(mktemp -d)"
cat > "$FAKE_BIN/pikpak" <<EOF
#!/usr/bin/env bash
exec python3 "$MOCK/mock_pikpak.py" "\$@"
EOF
chmod +x "$FAKE_BIN/pikpak"
trap 'rm -rf "$FAKE_BIN"' EXIT

export PATH="$FAKE_BIN:$PATH"
command -v pikpak >/dev/null || { echo "FAIL: 假 pikpak 未生效"; exit 1; }

echo "════ 冒烟 1：广告扫描器（真实 subprocess 路径）════"
python3 "$ROOT/skills/pikpak-ad-cleaner/scripts/scan_ads.py" --folder "" \
  --json /tmp/smoke_ad_report.json --emit-trash-cmd high
echo

echo "════ 冒烟 2：查重分析器（真实 subprocess 路径）════"
python3 "$ROOT/skills/pikpak-dedup/scripts/analyze_dupes.py" --folder "" \
  --policy newest --json /tmp/smoke_dup_report.json --emit-trash-cmd
echo

echo "════ 冒烟 3：报告文件校验 ════"
python3 - <<'PY'
import json
ad = json.load(open("/tmp/smoke_ad_report.json"))
dup = json.load(open("/tmp/smoke_dup_report.json"))
assert ad["source"] == "live", "广告报告应为 live 源（走 subprocess）"
assert len(ad["high"]) == 3 and len(ad["review"]) == 2 and len(ad["protected"]) == 2, ad
assert len(dup["groups"]) == 1 and dup["groups"][0]["count"] == 3, dup
ids = [t["id"] for t in dup["groups"][0]["trash_candidates"]]
assert ids == ["f_s1", "f_s3"], ids
print("报告校验通过：广告报告 high=3/review=2/protected=2；查重组 1 组 3 版本，清理候选 f_s1、f_s3")
PY

echo
echo "════ 冒烟 4：未登录错误路径 ════"
# 假命令模拟未登录
cat > "$FAKE_BIN/pikpak" <<'EOF'
#!/usr/bin/env bash
echo "Error: Failed to get list" >&2
echo "  Code: UNAUTHENTICATED" >&2
exit 1
EOF
chmod +x "$FAKE_BIN/pikpak"
set +e
OUT=$(python3 "$ROOT/skills/pikpak-ad-cleaner/scripts/scan_ads.py" --folder "" 2>&1)
RC=$?
set -e
echo "$OUT" | head -3
[ $RC -ne 0 ] && echo "$OUT" | grep -q "auth login" && echo "未登录错误路径 OK（提示先登录）"

echo
echo "全部冒烟测试通过。"
