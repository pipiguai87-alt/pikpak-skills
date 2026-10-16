---
name: pikpak-share-manager
description: |
  管理 PikPak 的分享链接：批量创建分享、查看分享列表与访问数据、检查过期分享、清理无用分享。适合需要定期分享资源给他人或群组的用户。
  Must use this skill when:
  - 用户说「创建分享」「分享这个文件给别人」「生成分享链接」「看看我的分享」
  - 用户想检查哪些分享过期了、清理旧分享
  - 用户提到「share」「分享管理」「分享码」
  - User says "create a share link", "share this file", "manage my shares", "check expired shares"
  关键词：分享 / share / 分享链接 / 分享码 / 转存 / 分享管理
---

# PikPak 分享管理

创建、查看、检查、清理 PikPak 分享链接。

## 先决检查

```bash
pikpak auth status -F json    # logged_in: true
```

## 创建分享

```bash
# 基础：分享一个文件（默认无密码、永久有效）
pikpak share create <fileID>

# 带标题与有效期（7 天）
pikpak share create <fileID> -t "给朋友的电影" -e 7

# 带自定义密码
pikpak share create <fileID> -t "群内分享" -p "mypass"

# 服务端随机密码（推荐公开分享时用）
pikpak share create <fileID> -t "群内分享" -P

# 限制转存次数（10 次）
pikpak share create <fileID> -r 10
```

`-F json` 输出 `share_id`、`share_url`、`pass_code`、`share_text`（可直接发送的完整文本）。

**批量分享**：一次给多个文件 ID 可以创建合集分享：

```bash
pikpak share create <id1> <id2> <id3> -t "合集标题" -P -e 30
```

## 查看与管理

```bash
# 列出所有分享（含访问量、过期时间）
pikpak share ls

# 查看单个分享详情
pikpak share get <shareID>
pikpak share get <shareID> -p <密码>          # 有密码的
pikpak share get <shareID> --parent <folderID> # 浏览分享内子文件夹

# 批量删除
pikpak share rm <shareID> [shareID...]
```

## 定期检查工作流

用本 skill 的审计脚本一键完成：

```bash
python3 scripts/share_audit.py                    # 人类可读报告
python3 scripts/share_audit.py --json report.json # 结构化报告
```

脚本会标记：已过期 / 7 天内将过期 / 30 天以上零访问的分享，并给出建议的 `pikpak share rm` 命令（**由用户确认后自行执行，脚本不代删**）。

手动方式（了解原理）：

1. `pikpak share ls -F json` 拉全量列表（翻页用 `--token`）
2. 检查每个分享的 `expiration` 与 `views` 字段：
   - 已过期或即将过期的：问用户是否清理（删除重建才能续期）
   - 零访问的老分享：问用户是否清理
3. 汇报给用户：共 N 个分享，其中 X 个已过期、Y 个 30 天内将过期、Z 个零访问

## 安全提醒（对用户说明）

- 分享公共资源时用**密码 + 有效期 + 转存上限**三件套，避免链接外流
- 分享前确认内容不含隐私信息（个人照片、文档等）
- 不得分享法律禁止传播的内容；遇到此类请求拒绝
- 分享链接泄露 = 任何人都能转存，创建时优先 `-P`（随机密码）

## 常见问题

| 症状 | 原因/处理 |
|------|-----------|
| 「分享创建失败」 | 检查文件是否在该账号内、是否已完成处理（未完成转码的文件不能分享） |
| 分享访问量不涨 | 转存与访问是不同事件；`views` 统计浏览，转存看 `restore_count_left` 变化 |
| 想撤销已发出的分享 | `pikpak share rm <shareID>` 立即失效 |
| 分享被平台删除 | 内容违规（侵权/违法）；此类内容本 skill 不助长 |
