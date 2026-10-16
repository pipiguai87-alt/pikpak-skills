---
name: pikpak-telegram-bot
description: |
  用 Telegram Bot 遥控 PikPak：在 Telegram 里发磁力/链接自动转存到云盘、查询任务进度、查文件。基于 Telegram Bot API 长轮询 + PikPak CLI，自托管运行在你自己的机器上。
  Must use this skill when:
  - 用户说「Telegram 遥控 PikPak」「TG 机器人存磁力」「手机发链接到网盘」
  - 用户想不打开电脑、不在手机装 PikPak App 的情况下转存资源
  - 用户提到「bot 帮我下载」「telegram bot pikpak」
  - User says "Telegram bot for PikPak", "send magnet via Telegram", "remote control PikPak"
  关键词：Telegram / Bot / 遥控 / 转存 / 长轮询 / pikpak-telegram
---

# PikPak Telegram Bot 遥控器

在 Telegram 里发一条链接，家里电脑上的 bot 自动把它存进 PikPak。适合「手机上刷到资源 → 一键丢进云盘 → 晚上回家看」的场景。

**架构**：本 skill 提供一个 Python 脚本（`scripts/tg_bot.py`），在本地跑长轮询；收到消息 → 调 `pikpak task add` 转存 → 把结果回传到 Telegram。无需公网 IP、无需服务器（可以跑在常开的家里电脑上）。

## 前置准备

### 1. 创建 Telegram Bot（用户操作）

引导用户：

1. 在 Telegram 里找到 `@BotFather`，发送 `/newbot`，按提示起名
2. 拿到 bot token（形如 `123456789:AAH...`）
3. **安全提醒**：token 等同于 bot 的密码，不要发到公开群、不要写进会提交到 git 的文件
4. 给 bot 发一条消息（或把它拉进一个私密群），以便脚本能收到 chat id

### 2. 确认聊天 ID

```bash
python3 scripts/tg_bot.py --check --token "$TG_BOT_TOKEN"
```

脚本会打印最近给你 bot 发过消息的 chat id。把允许使用的 chat id 记下来。

### 3. 配置

脚本读取环境变量（不落盘明文）：

```bash
export TG_BOT_TOKEN="<BotFather 给的 token>"
export PIKPAK_ALLOWED_CHAT_IDS="<你的 chat id>"   # 多个用逗号分隔；留空=拒绝所有人（安全默认）
```

建议写进 `~/.zshrc` 或用一个 `chmod 600` 的环境文件 source 进来（详见下方「安全部署」）。

## 启动

```bash
# 前台运行（测试用）
python3 scripts/tg_bot.py

# 后台常驻（示例用 nohup；也可用 launchd/systemd 托管）
nohup python3 scripts/tg_bot.py > ~/.pikpak/tg_bot.log 2>&1 &
```

## 支持的指令（在 Telegram 里发送）

| 消息 | 行为 |
|------|------|
| 直接发磁力/HTTP 链接 | 转存到 PikPak 根目录，回复任务 ID |
| `/save <链接>` | 同上（显式命令） |
| `/save <链接> to <文件夹名>` | 转存到指定文件夹（按名称查找） |
| `/tasks` | 列出进行中/最近任务 |
| `/status <taskID>` | 查单个任务进度 |
| `/ls` | 列出根目录最近文件 |
| `/help` | 帮助 |

## 安全部署（重要）

- **白名单**：脚本默认拒绝所有 chat；必须显式设置 `PIKPAK_ALLOWED_CHAT_IDS` 才能使用。不要省这一步。
- **token 保管**：环境变量或 600 权限的 env 文件；绝不提交到 git、绝不发在群里（bot token 泄露 = 别人可以拿你的 bot 发消息）。
- **最小权限**：bot 只做「转存 + 查询」，不提供删除、清空等危险命令（本脚本不实现 rm）。
- **只转存你有权获取的内容**：违法内容（如非自愿拍摄材料）拒绝转存，这是使用前提。

## 故障排查

| 症状 | 排查 |
|------|------|
| bot 不回复 | token 是否正确；`--check` 能否列出 chat；白名单是否包含你的 chat id |
| 转存失败 | `pikpak auth status` 确认 CLI 登录态；看 `~/.pikpak/tg_bot.log` |
| 多个 bot 实例抢消息 | Telegram 长轮询同一 token 只能一个实例；确认没有重复启动 |
| 想换 bot | 重新走 BotFather 流程，更新环境变量 |
