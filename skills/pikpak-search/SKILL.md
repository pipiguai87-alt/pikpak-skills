---
name: pikpak-search
description: |
  在 PikPak 云盘里按文件名/路径搜索文件（补官方 CLI 已下线的 search 功能）。支持多关键词（AND/OR）、正则、类型过滤、按大小/时间排序。
  Must use this skill when:
  - 用户问「云盘里有没有 XX」「帮我找找 XX 文件」「搜一下 XX」
  - 用户想按关键词/分辨率/年份找资源（如「找所有 4K 的」「找含国语的」）
  - 用户说「search my drive」「find a file in PikPak」「where is XX」
  - 官方 search 命令不可用时（服务端 API 已下线），用本 skill 的遍历方案替代
  关键词：搜索 / 查找 / 找文件 / search / find / 云盘搜索
---

# PikPak 云端搜索

官方 CLI 的 `pikpak search` 已因服务端 API 下线而不可用；本 skill 用「遍历 + 本地匹配」补上这个缺口（代价是速度取决于目录规模，适合几万文件以内）。

## 先决检查

```bash
pikpak auth status -F json    # logged_in: true
```

## 基本用法

```bash
# 按关键词搜（大小写不敏感）
python3 scripts/search_files.py --folder "" --query "Sintel"

# 多个关键词（OR：命中任一）
python3 scripts/search_files.py --folder "" --query "国语 粤语 中字"

# 多个关键词（AND：全部命中）
python3 scripts/search_files.py --folder "" --query "4k 国语" --mode and

# 正则
python3 scripts/search_files.py --folder "" --query "\.(mkv|mp4)$" --regex

# 排序与限制
python3 scripts/search_files.py --folder "" --query "Sintel" --sort size --desc --limit 20
```

## 常用搜索场景

| 用户说 | 命令 |
|--------|------|
| 「找所有 4K 的片子」 | `--query "2160p 4K"` |
| 「找含国语音轨的」 | `--query "国语"` |
| 「找某个演员/系列的」 | `--query "名字"` |
| 「找体积最大的几个文件」 | 配合 storage-report；或 `--query "" ` 不适用——用 `--sort size` 前先搜宽泛词 |
| 「找 2024 年之后的」 | 用 `--sort time` 看时间倒序结果 |
| 「找某个文件夹」 | `--type folder --query "名称"` |

## 工作流

1. **确定搜索范围**：根目录全盘（默认）还是某个子目录（`--folder <id>`）。大目录建议先缩小范围。
2. **执行搜索**：脚本遍历目录树（每页 500 项自动翻页），逐个匹配文件名与完整路径。
3. **展示结果**：按用户偏好排序（名称/大小/时间），给路径让用户能定位。
4. **后续操作**：找到后可接 `pikpak play <id>` 播放、`pikpak download <id>` 下载、或交给别的 skill 处理。

## 性能说明（诚实告知用户）

- 搜索是**遍历式的**（官方无索引 API）：几万文件约需几十秒到几分钟
- 有安全上限（5 万文件/5000 目录），超限会中止并提示缩小范围
- 结果只匹配**文件名与路径**，不搜内容

## 安全边界

- 只读：仅调用 `pikpak ls`
- 不修改、不删除任何文件
- 搜索词由用户提供，脚本不做内容判断
