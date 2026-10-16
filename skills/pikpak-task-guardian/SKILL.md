---
name: pikpak-task-guardian
description: |
  PikPak 离线任务守护：监控进行中的云下载任务，失败自动重试、完成后本地通知、生成每日任务报告。适合经常挂一堆磁力任务、不想反复手动检查的用户。
  Must use this skill when:
  - 用户说「任务失败了没注意」「下完了能不能通知我」「自动重试」「任务多了管不过来」
  - 用户想定时检查云下载进度、汇总任务状态
  - 用户提到「追更」「监控任务」「task guardian」「任务守护」
  - User says "monitor my download tasks", "notify when done", "retry failed tasks", "task guardian"
  关键词：任务 / 监控 / 重试 / 通知 / 追更 / task
---

# PikPak 任务守护

给云下载任务上一个「守护进程」：定期巡检，失败的自动重试，完成的推送通知，还会给你日报。

## 先决检查

```bash
pikpak auth status -F json    # logged_in: true
```

## 单次巡检

```bash
python3 scripts/task_guardian.py --once
```

输出：进行中任务数、失败任务列表、自动重试结果、昨日完成统计。

## 常驻运行（推荐）

```bash
# 前台（测试）
python3 scripts/task_guardian.py --interval 300

# 后台常驻
nohup python3 scripts/task_guardian.py --interval 300 > ~/.pikpak/guardian.log 2>&1 &
```

每 5 分钟（`--interval`）巡检一次，状态变化时：
- **新失败任务** → 自动重试（`task rm` + `task add` 重提交源链接）；重试上限 3 次
- **新完成任务** → macOS 通知（`osascript`）；其他平台打印日志
- **每日 09:00** → 前一日任务汇总报告

## 定时（可选，配合 launchd/cron）

macOS launchd 示例（每次跑一轮巡检）：

```bash
# ~/Library/LaunchAgents/com.user.pikpak-guardian.plist
# StartInterval = 300，ProgramArguments = python3 .../task_guardian.py --once
```

或 crontab：

```
*/5 * * * * /usr/bin/python3 ~/.hermes/skills/pikpak-task-guardian/scripts/task_guardian.py --once >> ~/.pikpak/guardian.log 2>&1
```

## 工作原理

1. `pikpak task ls -F json` 拉取任务列表（含进行中与最近失败）
2. 对 `error` 状态的任务：
   - 从 `task get` 拿源信息（`params.url` 记录在对应文件上；任务本身有 ID 与名称）
   - **注意**：CLI 不直接暴露任务的原始磁力——脚本维护本地 `task_sources.json`（从 `/save` 历史记录），对无源任务只通知不重试
3. 对 `complete` 的新任务发通知
4. 状态存 `~/.pikpak/guardian_state.json`，避免重复通知

## 诚实的限制

- **重试能力有限**：CLI 不提供任务源链接查询，脚本只能重试它见过的源（通过 bot 或手动配置登记的）；纯粹在 App 里创建的任务失败时只能通知你手动处理
- 补一个方案：把常用源放 `~/.pikpak/watch_sources.json`（数组，元素 `{"url": "...", "note": "..."}`），守护脚本看到它们对应的任务失败/消失时自动重提
- 通知依赖系统能力（macOS `osascript`）；无声环境降级为日志

## 安全边界

- 守护脚本只做 `task ls` / `task get` / `task add` / `task rm`（仅清理失败任务以便重试）
- 不删除文件、不清空、不修改用户资源
- 不自动添加新资源（除用户配置的 watch 源重试）；不主动搜索或订阅任何内容源
