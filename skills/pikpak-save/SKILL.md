---
name: pikpak-save
description: |
  把磁力链接 / HTTP 链接 / 分享链接一键存入 PikPak 云盘，自动跟踪下载进度，完成后直接播放或下载到本地。支持批量转存与分享链保存。
  Must use this skill when:
  - 用户发来磁力链接（magnet:）、BT 种子、下载链接说「存到 PikPak」「离线下载」「云下载」
  - 用户想保存别人分享的 PikPak 分享链接（share）
  - 用户说「帮我下载这部片/这个资源到网盘」「一键转存」
  - User says "save to PikPak", "cloud download this", "offline download", "add magnet", "restore this share"
  关键词：磁力链接 / 云下载 / 离线下载 / 转存 / 保存到网盘 / task add / share restore
---

# PikPak 秒存助手

一条命令把磁力/链接/分享链存进 PikPak，跟踪到完成，接着就能看。**负责「发现资源 → 落到云盘 → 开始观看」的最短路径。**

## 先决检查

```bash
pikpak auth status -F json    # 确认 logged_in: true；否则先 pikpak auth login
```

## 场景一：磁力 / HTTP 链接转存

### 1. 提交任务

```bash
pikpak task add "<链接>" -w
```

- `-w` 会实时显示进度直到完成（大任务可能较久，耐心等或改用无 `-w` 分步轮询）
- 指定目标文件夹加 `-p <folderID>`
- 返回中会带 `task_id` 和 `file_name`——保留 task_id 用于后续查询

### 2. 进度查询（长任务）

```bash
pikpak task ls -s running,pending     # 有任务在跑吗
pikpak task get <taskID>              # 单任务详情（含子任务进度）
pikpak task watch <taskID>            # 实时监控（Ctrl+C 退出）
```

### 3. 完成后拿到文件

```bash
pikpak task get <taskID> -F json      # 取 file_id
pikpak play <fileID>                  # 在线播放
pikpak download <fileID>              # 下载到本地
```

## 场景二：保存别人的分享链接

用户给出分享链接或分享 ID：

```bash
pikpak share get <shareID>                        # 先看内容（有密码加 -p <密码>）
pikpak share restore <shareID>                    # 全部转存到自己云盘
pikpak share restore <shareID> -p <密码>          # 带密码
pikpak share restore <shareID> --parent <folderID>  # 转存到指定文件夹
pikpak share restore <shareID> -f "id1,id2"       # 只转存部分文件
```

转存后查看历史：`pikpak share history`

## 场景三：批量转存

用户一次给多个链接时逐个提交，循环执行：

```bash
pikpak task add "<链接1>"
pikpak task add "<链接2>"
```

然后统一查看：`pikpak task ls -s running,pending`

## 转存后的常见追问

| 用户说 | 做什么 |
|--------|--------|
| 「存到哪了」 | `pikpak ls` 翻目录（任务默认存根目录或指定 `-p` 位置） |
| 「下载不下来/没速度」 | 转存 vs 下载的区别：`pikpak task add` 是云下载（服务器下）；`pikpak download` 才是拉到本地。确认用户要哪个 |
| 「怎么在线看」 | `pikpak play <fileID>` 或 PikPak App 里直接看 |
| 「任务卡住了」 | `pikpak task get <taskID>` 看状态；error 时把 message 给用户；可 `pikpak task rm <taskID>` 清掉重试 |

## 注意事项

- **每日任务上限**：免费用户有每日云下载次数限制（`DAILY_CREATE_LIMIT_FREE`），会员更高。遇到报错如实告知，不绕过。
- **转存他人的分享**不占用云下载次数，是更省额度的路径。
- 磁力资源包可能夹带广告文件——转存完可提示用户用 `pikpak-ad-cleaner` 清理。
- 只帮用户转存其有权获取的内容；盗版、违法内容（如非自愿拍摄）一律拒绝。
