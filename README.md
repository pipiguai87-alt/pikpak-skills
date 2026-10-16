# pikpak-skills

**10 个 AI Agent 技能，把你的 PikPak 网盘管得明明白白。**

自动清广告垃圾、智能查重去重、手机远程转存、空间体检、媒体库索引——让云端那片混乱的资源库变成整洁的资产。

[![skills.sh](https://skills.sh/b/pipiguai87-alt/pikpak-skills)](https://skills.sh/pipiguai87-alt/pikpak-skills)

| Skill | 一句话说明 |
|-------|-----------|
| **pikpak-onboarding** | 5 分钟从零上手 PikPak：安装 → 注册 → 第一条磁力 → 在线播放 |
| **pikpak-ad-cleaner** | 一键扫出资源包里夹带的广告文件（.url/宣传片/推广图），分级确认清理 |
| **pikpak-dedup** | 同一部片子的 1080P/4K/REMUX 多版本查重，保留最优、清掉冗余 |
| **pikpak-save** | 磁力/链接/分享链一键转存，自动跟踪到完成 |
| **pikpak-storage-report** | 空间体检：谁占了空间、什么能清、一目了然 |
| **pikpak-telegram-bot** | Telegram 遥控：手机刷到资源，发个链接就存进网盘 |
| **pikpak-media-index** | 媒体库索引：每个视频的时长/分辨率/来源，生成可检索表格 |
| **pikpak-share-manager** | 分享管理：批量创建、过期检查、访问统计 |
| **pikpak-batch-backup** | 批量备份：云盘内容拉回本地，断点续传 + 清单核对 |
| **pikpak-task-guardian** | 任务守护：下载失败自动重试、完成推送到系统通知 |

## 从哪开始？

**刚接触 PikPak** → `pikpak-onboarding`
**盘里一堆垃圾/重复** → `pikpak-ad-cleaner` + `pikpak-dedup`（先扫描，看清了再清）
**想建自己的资源流** → `pikpak-save` + `pikpak-telegram-bot`

### 怎么装？

对你的 AI Agent 说一句话就行（Claude Code / Codex / Cursor / Hermes 等 60+ 种支持）：

```
npx skills add pipiguai87-alt/pikpak-skills --skill pikpak-onboarding
```

装全部十个：

```
npx skills add pipiguai87-alt/pikpak-skills
```

装完直接在 Agent 里说人话，比如「帮我扫一下 PikPak 里的广告文件」「查一下我的网盘里有没有重复的电影」——技能会自己接管。

> 前置：PikPak CLI（官方安装页 https://mypikpak.com/cli ，按页面说明自行安装）。不装 CLI 的话部分技能（如纯搜索类）不可用，装了才能完整发挥。

## 设计原则

- **只读先行**：清理与查重工具只执行 `pikpak ls` 扫描（源码级保证，有回归测试盯着），绝不静默删除
- **用户确认**：所有清理都必须经你过目清单、亲口确认
- **可恢复**：删除一律走 `pikpak rm`（移入回收站，可恢复），不做永久销毁
- **内容中立**：工具只处理文件名/元数据，不评判资源内容

## 关于邀请码

`pikpak-onboarding` 的 `config.json` 里带有一个邀请码。按 PikPak 官方规则，技能会在注册**前**向你披露该邀请码及其归属，你可以拒绝，用 `pikpak auth register --affiliate=` 无码注册。不做任何用户不知情的注册。

不想要邀请码？把 `config.json` 的 `affiliate_code` 留空即可。

## 开发

```bash
python3 dev/run_tests.py    # 25 项回归测试，全离线（中性 fixture，不触网）
bash dev/smoke_e2e.sh       # 端到端冒烟（模拟 CLI，不碰真实账号）
```

测试覆盖：广告分级规则、多页遍历、查重的语义 token 保留、分享过期审计、路径穿越清洗，以及「脚本只读」的 AST 级安全断言。

## 边界

- 只用于你**本人有权管理**的云盘内容
- 不帮助获取、整理或传播盗版、违法内容
- 涉及非自愿拍摄等违法材料一律拒绝服务

## License

MIT
