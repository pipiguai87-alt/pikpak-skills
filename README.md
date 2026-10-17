# pikpak-skills

**14 个 AI Agent 技能，把你的 PikPak 网盘管得明明白白。**

广告文件分级扫描、多版本候选分析、远程转存、空间体检与速度对照测量。清理先报告再确认；第三方方案和实际性能分别验证，不承诺固定提速。

[![skills.sh](https://skills.sh/b/pipiguai87-alt/pikpak-skills)](https://skills.sh/pipiguai87-alt/pikpak-skills)

| Skill | 一句话说明 | 当前成熟度（均未作生产推荐） |
|-------|-----------|----------------------------|
| **pikpak-onboarding** | 分步安装与注册引导；注册前披露引荐关系，明确允许拒绝 | 实验：操作卡与离线计划器；真实注册未验 |
| **pikpak-ad-cleaner** | 广告候选分级扫描；冻结报告与逐 ID 批准后仅生成命令预览 | 实验：离线扫描与批准预览；真实账号未验 |
| **pikpak-dedup** | 按媒体类型隔离的多版本候选分析；名称相似不冒充内容相同 | 实验：离线候选分析；不证明内容重复 |
| **pikpak-save** | 磁力/链接/分享转存操作卡；离线检查精确任务 ID 与下一步 | 实验：离线状态检查；真实保存闭环未通过 |
| **pikpak-storage-report** | 目录/大文件排行与未知大小提示；不把枚举结果当账号配额 | 实验：离线列表汇总；真实配额未验 |
| **pikpak-telegram-bot** | 双重白名单遥控、持久消息去重；提交结果未知时不盲目重放 | 实验：离线授权、去重、退避；联机未验 |
| **pikpak-media-index** | 视频元数据与来源索引；CSV 公式中和、敏感 URL 参数不导出 | 实验：离线导出；真实 CLI schema 未验 |
| **pikpak-share-manager** | 只读分享审计：完整分页、过期/临期/零访问；未知字段保留未知 | 实验：离线审计；真实分享列表未验 |
| **pikpak-batch-backup** | 单次批量下载与本地清单；拒绝路径冲突、覆盖和短文件，不支持续传 | 实验：离线下载安全；真实备份未验 |
| **pikpak-task-guardian** | 默认仅观察；通知、精确登记源重提交分别授权，预算/退避持久化 | 实验：离线守护策略；真实常驻运行未验 |
| **pikpak-search** | 按关键词/正则遍历文件或目录；CLI 没有 search 不代表服务端搜索下线 | 实验：离线遍历；真实 CLI schema 未验 |
| **pikpak-magnet-pipeline** | 单文件分步状态管道；下载收据绑定清理预览，不自动删除 | 实验：离线分步管道；真实闭环未验 |
| **pikpak-webdav-guide** | 官方 WebDAV 开通、专用凭据、客户端与配额；默认不查询账号 | 实验：操作卡与离线元数据；真实挂载未验 |
| **pikpak-speed-guide** | 无账号连通性探测 + 完整性门禁后的 A/B 片段测速；不推断 QoS 根因 | 实验：本机 HTTP 样本；真实吞吐未验 |

以上逐项展示不等于可用推荐。只有相应用途完成合法中性数据的真实闭环与关键安全复审，才会提升推荐等级；fixture、假 CLI、测试数量和目录收录不能替代。

## 从哪开始？

**刚接触 PikPak** → `pikpak-onboarding`
**盘里一堆垃圾/重复** → `pikpak-ad-cleaner` + `pikpak-dedup`（先扫描，看清了再清）
**想建自己的资源流** → `pikpak-save` + `pikpak-telegram-bot`
**找文件/搜资源** → `pikpak-search`
**磁力下载到本地、核验后预览云端清理** → `pikpak-magnet-pipeline`
**下载慢或播放拖动卡顿** → `pikpak-speed-guide`（先测量，后选择方案）
**接入标准 WebDAV 客户端** → `pikpak-webdav-guide`（会员功能，有配额限制）

### 怎么装？

在支持 Agent Skills 的工具中安装，例如 Claude Code / Codex / Cursor / Hermes：

```
npx skills add pipiguai87-alt/pikpak-skills --skill pikpak-onboarding
```

安装全部技能：

```
npx skills add pipiguai87-alt/pikpak-skills
```

装完直接在 Agent 里说人话，比如「帮我扫一下 PikPak 里的广告文件」「查一下我的网盘里有没有重复的电影」——技能会自己接管。

> 前置：PikPak CLI（官方安装页 https://mypikpak.com/cli ，按页面说明自行安装）。不装 CLI 的话部分技能（如纯搜索类）不可用，装了才能完整发挥。

### 也可以在 ClawHub 安装

```
clawhub install @pipiguai87-alt/pikpak-onboarding
```

14 个 skill 全部已发布到 ClawHub，slug 与目录名一致，例如 `@pipiguai87-alt/pikpak-speed-guide`。

### 直接说人话就能触发

| 你说 | 用到的 skill |
|---|---|
| 帮我扫一下网盘里的广告文件 | `pikpak-ad-cleaner` |
| 查一下有没有重复的电影 | `pikpak-dedup` |
| 这个磁力帮我存到 PikPak | `pikpak-save` |
| 我网盘哪些文件最占地方 | `pikpak-storage-report` |
| 下载好慢 / 拖动一直卡 | `pikpak-speed-guide` |
| 怎么用 WebDAV 挂到 Infuse | `pikpak-webdav-guide` |
| 任务总是失败，帮我盯着 | `pikpak-task-guardian` |
| 用 Telegram 遥控存链接 | `pikpak-telegram-bot` |
| 在网盘里搜某个文件 | `pikpak-search` |
| 给视频建一份索引表 | `pikpak-media-index` |
| 把整个文件夹备份到本地 | `pikpak-batch-backup` |
| 查分享链接有没有过期 | `pikpak-share-manager` |
| 磁力下到本地再清云端 | `pikpak-magnet-pipeline` |
| 第一次用 PikPak，怎么开始 | `pikpak-onboarding` |

## 设计原则

- **只读先行**：清理与查重工具只执行 `pikpak ls` 扫描（源码级保证，有回归测试盯着），绝不静默删除
- **用户确认**：所有清理都必须经你过目清单、亲口确认
- **不自动删除**：只生成经批准的命令预览；`rm` 的恢复与容量语义须独立核验，不承诺已释放空间
- **内容中立**：工具只处理文件名/元数据，不评判资源内容

## 真实能力与验证边界

- 当前为离线验证版：fixture、假 CLI 和本机 HTTP 回归不代表真实账号、官方 JSON 字段或实际速度已经验收。
- 每个技能的 `references/upstream-design.md` 记录固定 GitHub 源码、许可证、采纳/拒绝理由与未验收项；没有捆绑运行这些上游代码。
- `rclone` 是可选外部客户端，原生 PikPak 与标准 WebDAV 是不同 backend；小型加速代理没有因自报测速而成为默认生产方案。
- 不提供未实现的断点续传、增量备份、跨名称内容查重或每日定时报表。链接转存与分享创建等部分能力仍是人工审核操作卡。
- 自有账号、外部发送、实际下载、通知、重提交和清理均需要对应的当次授权；不因安装技能获得授权。

## 关于邀请码

每个 `skills/<name>/SKILL.md` 都直接包含本套公开引荐披露：公开码为 `22874035`，使用可能让分享者获得推广奖励，福利以 PikPak 官方页面为准；你可以拒绝，不影响对应 skill。只有 `pikpak-onboarding` 提供注册前的 `registration_plan.py` 计划入口，其他 13 个 skill 不自动注册、不登录，只链接回 onboarding。

同意时由用户在知情后执行：`pikpak auth register --affiliate=22874035`。拒绝或留空时必须显式执行：`pikpak auth register --affiliate=`。只省略参数或清环境变量可能继承安装器记录，不能当可靠拒绝。逐项证据与回归保存在仓库外的私人审计记录中；真实注册、账号兼容和公开 registry 更新仍分别验收。

## 开发

```bash
python3 dev/run_tests.py    # fixture + loopback HTTP 回归，不访问真实账号或互联网
bash dev/smoke_e2e.sh       # 端到端冒烟（模拟 CLI，不碰真实账号）
```

测试覆盖各项专属回归：审批范围、分页/目录环、跨类型候选、未知元数据、路径/下载完整性、任务去重/退避、授权/通知与速度测量。统一 runner 隔离 HOME/PATH/PIKPAK_BIN，默认拒绝子进程，只放行精确核准的假 CLI 和采样 worker，并经隔离 bootstrap 将 Python 审计限制带入子解释器；受覆盖的 Python socket、DNS、进程和通知入口只允许获准的中性 loopback 流量。这是防误调用措施，不是 OS 沙箱，也不承诺拦住恶意原生代码逃逸。冒烟检查强制自建假 CLI，不继承真实 CLI 覆盖变量。可选 Markdown 渲染检查需要已有解析器，缺失时明确跳过、不算通过。测试通过不代表真实账号兼容或真实网盘提速。

## 边界

- 只用于你**本人有权管理**的云盘内容
- 不帮助获取、整理或传播盗版、违法内容
- 涉及非自愿拍摄等违法材料一律拒绝服务

## License

MIT
