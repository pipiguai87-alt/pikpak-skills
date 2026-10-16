---
name: pikpak-batch-backup
description: |
  把 PikPak 云盘内容批量备份/下载到本地：按文件夹或清单批量下载、断点续传、生成备份清单与校验信息。适合定期把重要云盘内容拉回本地的用户。
  Must use this skill when:
  - 用户说「云盘备份到本地」「批量下载 PikPak」「把整个文件夹拉下来」
  - 用户想定期备份云盘重要内容到移动硬盘/本地
  - 用户提到「backup」「批量下载」「转存到本地」
  - User says "backup PikPak to local", "bulk download from cloud", "save all files locally"
  关键词：备份 / 批量下载 / 断点续传 / 拉回本地 / backup
---

# PikPak 批量备份到本地

把云盘文件夹（或指定清单）批量拉回本地磁盘，支持断点续传与备份清单核对。

## 先决检查

```bash
pikpak version                        # CLI 已安装
pikpak auth status -F json            # logged_in: true
test -w /目标/备份目录 || echo "目标不可写"
```

## 方式一：单文件/少量文件

```bash
pikpak download <fileID> -o ./downloads/     # 指定输出路径（单文件）
pikpak download <id1> <id2> <id3>            # 多个文件（保持原名，存当前目录）
```

## 方式二：批量备份（用脚本）

```bash
# 把整个文件夹树拉下来（保持目录结构、断点续传）
python3 scripts/batch_backup.py --folder "<folderID>" --dest /Volumes/Backup/pikpak

# 先演练（只列出会下载什么，不实际下载）
python3 scripts/batch_backup.py --folder "<folderID>" --dest /Volumes/Backup/pikpak --dry-run

# 断点续传（再次运行同命令，已下载的会跳过）
python3 scripts/batch_backup.py --folder "<folderID>" --dest /Volumes/Backup/pikpak
```

脚本能力：
- 遍历云盘目录树 → 逐文件调用 `pikpak download`（CLI 自带断点续传能力）
- 跳过已存在且大小一致的文件（快速断点续传）
- 生成 `backup_manifest.json`：文件清单、大小、哈希（如云盘侧提供）、下载状态
- 失败自动记录，最后汇总报告

## 方式三：只要下载链接（用外部工具下载）

```bash
pikpak download <fileID> --url-only        # 输出直链，可交给 aria2/IDM 等
pikpak ls --raw -l 500 | jq -r '.files[] | select(.kind=="drive#file") | .id' | head -20
```

适合用户已有下载管理器的情况；链接有时效，需及时使用。

## 备份策略建议（给用户）

| 场景 | 建议 |
|------|------|
| 重要文档/照片 | 每月全量备份 + 清单核对（manifest 对比） |
| 大体积影视资源 | 按需下载或只备份清单（100GB+ 不推荐全拉） |
| 增量备份 | 记录上次清单，只下新增/修改的文件（脚本生成清单后可 diff） |
| 移动硬盘备份 | 先 `--dry-run` 看总量，确认空间够 |

## 转录配额注意

- 下载走 PikPak 的**下行流量配额**（月度 4 TB 起）
- CLI 属于「已连接的应用」，共享 25% 配额上限（下行约 1 TB/月）
- 大批量备份前用 `pikpak auth status -F json` 看当前用量，避免中途触发限速
- 限速时下载降到约 100 KB/s——如实告知用户，等配额重置

## 安全边界

- 只备份用户自己的云盘内容；不引导获取他人隐私或违法内容
- 备份目录由用户指定；脚本不自动删除本地或云端任何文件
- 备份清单文件含文件名与路径——提醒用户勿把清单随意公开（可能泄露隐私信息）
