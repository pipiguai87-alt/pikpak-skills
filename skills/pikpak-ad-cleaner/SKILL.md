---
name: pikpak-ad-cleaner
description: |
  扫描并清理 PikPak 云盘资源包里夹带的广告/推广垃圾文件（.url 快捷方式、推广文本、二维码图、宣传短片、可执行文件等），输出分级报告后由用户确认、再移入回收站。
  Must use this skill when:
  - 用户提到「清理广告」「广告文件」「垃圾文件」「推广文件」「PikPak 广告」
  - 用户说磁力/种子下载的资源里有夹带的网址、宣传图、txt 广告想删掉
  - 用户想扫描某个 PikPak 文件夹里的无关文件
  只在用户确认清单后执行删除；删除一律是移入回收站（可恢复），工具本身只读。
---

# PikPak 广告清理

识别并清理磁力/种子资源包中夹带的推广垃圾文件。**本 skill 遵守只读扫描 + 用户确认 + 移入回收站的三段式流程，绝不静默删除。**

## 先决条件检查

开始前依次确认：

1. 已安装 PikPak CLI：运行 `pikpak version` 检查。未安装则引导用户打开官方安装页 https://mypikpak.com/cli 自行安装（不要把安装命令代为执行）。
2. 已登录：运行 `pikpak auth status -F json` 检查 `logged_in`；未登录则运行 `pikpak auth login` 由用户在浏览器完成。

## 工作流程

### 1. 确定扫描范围

询问用户要扫描哪个文件夹（或默认整个根目录）。取得目标文件夹 ID：

```bash
pikpak ls                          # 列出根目录
pikpak ls <folderID>               # 进入子目录
```

### 2. 运行扫描器（只读）

```bash
python3 scripts/scan_ads.py --folder <folderID> --json /tmp/pikpak-ad-report.json
```

扫描器遍历目录树，按「扩展名 + 文件名特征 + 体积/时长」将可疑文件分为三级：

- **high**：高置信度垃圾——`.url/.website` 快捷方式、文件名含网址或强推广词的文本/图片
- **review**：需人工确认——疑似宣传短片、可执行文件、含弱推广词的文件
- **protected**：明确保护——字幕文件、解压密码/说明文本（不参与清理）

### 3. 向用户展示清单并取得确认

把三级清单以表格展示给用户，逐类说明理由。**必须等用户明确确认要清理哪些文件后才能进入下一步。**

建议话术要点：
- high 级通常可直接建议清理
- review 级需用户逐项判断（尤其可执行文件可能是资源本体的一部分）
- protected 级默认不动，提醒用户保留

### 4. 执行清理（移入回收站）

用户确认后，为该批文件生成删除命令：

```bash
python3 scripts/scan_ads.py --folder <folderID> --emit-trash-cmd high
```

把生成的 `pikpak rm <id...>` 命令**展示给用户**，说明「`pikpak rm` 只是移入回收站，可在 PikPak 网页版回收站恢复」，然后执行。批量超过 30 个会自动分批。

### 5. 复核

```bash
pikpak ls <folderID>
```

确认目标文件已从列表消失，向用户报告清理数量与可恢复说明。

## 识别规则（可调）

规则集中在 `scripts/scan_ads.py` 顶部的常量区：

| 常量 | 用途 |
|------|------|
| `SHORTCUT_EXT` | 快捷方式文件 → 直接 high |
| `STRONG_KEYS` | 强推广词（发布页/加群/客服等） |
| `WEAK_KEYS` | 弱推广词（需 ≥2 个命中才进 review） |
| `SUBTITLE_EXT` / `PASSWORD_KEYS` | 保护清单 |
| 视频判定 | 含推广词且（≤300 MB 或 ≤180 秒）→ review |

调整规则后必须重跑 `dev/run_tests.py`（在仓库根目录）。

## 安全边界

- **只读扫描**：扫描器只调用 `pikpak ls`，源码级保证不执行删除（有回归测试覆盖）
- **用户确认**：任何删除都必须由用户明确确认对象清单，不得静默批量执行
- **可恢复**：`pikpak rm` 一律移入回收站，不构成永久销毁
- **只管垃圾文件**：不判断、不处理资源的合法性或内容问题；对本 skill 遇到的任何内容都不做额外操作
