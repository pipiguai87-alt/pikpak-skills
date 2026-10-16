---
name: pikpak-magnet-pipeline
description: |
  PikPak 磁力全链路管道：磁力 → 云下载 → 完成任务 → 拉回本地 → 释放云端空间。四步可单独执行、可检查、可中断续跑。
  灵感来自 GitHub 高星项目 PikPakAutoOfflineDownloadBot（511⭐）的「来去无痕迹」流程，用官方 CLI 合规实现。
  Must use this skill when:
  - 用户说「磁力下载到本地」「临时用一下网盘」「下完就删」「不占用网盘空间」
  - 用户想要「磁力→本地」一条龙：云端加速下载 + 拉回本地 + 清理
  - 用户提到「磁力管道」「magnet pipeline」「下完自动清理」
  - User says "download magnet to local then clean up", "temporary cloud download", "magnet to disk pipeline"
  关键词：磁力 / 管道 / 云下载 / 拉回本地 / 清理 / 来去无痕迹
---

# PikPak 磁力管道

把「磁力链接变成电脑上的文件、云端不留垃圾」做成一条四步流水线。**每一步单独可跑、失败可重入、清理需确认。**

适用场景：老磁力本地下不动 → 用 PikPak 云端秒下 → 拉回本地 → 清掉云端副本释放空间。

## 先决检查

```bash
pikpak auth status -F json    # logged_in: true
```

## 四步流水线

### Step 1：提交磁力到云端

```bash
python3 scripts/magnet_pipeline.py step1-add "magnet:?xt=urn:btih:..." 
# 可选指定云盘目标目录：--folder <folderID>
```

输出 `task_id`，进入第 2 步。

### Step 2：查询任务（可反复执行）

```bash
python3 scripts/magnet_pipeline.py step2-status <taskID>
```

- 状态 `complete` → 输出 `file_id`，进入第 3 步
- 状态 `running/pending` → 稍后再查
- 状态 `error` → 查看 message；可 `pikpak task rm <taskID>` 后从 Step 1 重来

### Step 3：拉回本地

```bash
python3 scripts/magnet_pipeline.py step3-download <fileID> --dest ./downloads
```

大文件耗时较长（CLI 自带断点续传，中断后可重跑）。

### Step 4：清理云端（生成命令，人工确认后执行）

```bash
python3 scripts/magnet_pipeline.py step4-cleanup <fileID>
```

脚本**只打印**建议的 `pikpak rm` 命令——由用户确认后自行执行。删除走回收站（可恢复）。

## 完整示例

```bash
# 1. 提交
python3 scripts/magnet_pipeline.py step1-add "magnet:?xt=urn:btih:XXX"
#    → task_id: TASK123

# 2. 等待完成后查
python3 scripts/magnet_pipeline.py step2-status TASK123
#    → file_id: FILE456

# 3. 下载
python3 scripts/magnet_pipeline.py step3-download FILE456 --dest ~/Downloads

# 4. 本地确认文件完好后，清理云端
python3 scripts/magnet_pipeline.py step4-cleanup FILE456
#    然后手动执行输出的 pikpak rm 命令
```

## 重要提醒（设计取舍）

- **不建议对主力账号做「下完即删」的自动化**：源项目 PikPakAutoOfflineDownloadBot 的作者也警告「删除机制强劲、容易误删、不建议用于主账号」。本管道坚持「清理步骤人工确认」正是为了规避这一点。
- **每日云下载次数限制**：免费账号有 `DAILY_CREATE_LIMIT_FREE`，失败时如实告知用户。
- **下行配额**：拉回本地消耗下行流量（CLI 属已连接应用，共享配额），大文件前先 `pikpak auth status` 看余量。
- **跨设备续跑**：每一步的输出（task_id / file_id）都可记录后续跑。

## 安全边界

- `step1/2/3` 直接执行（下载类操作）；`step4` 只生成命令不执行
- 删除一律走回收站；脚本源码不含删除调用
- 只处理用户有权获取的内容；违法内容拒绝服务
