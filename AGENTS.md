# pikpak-skills — Agent 工作规则

## 定位

PikPak 云盘的 14 个只读优先 agent skill，每个目录 `skills/pikpak-*` 是独立可分发的 skill 单元（SKILL.md + references/ + scripts/）。本仓库同时是它们的发布源：GitHub、ClawHub、skills.sh 从同一棵树分发。

## 怎么跑

```bash
python3 dev/run_tests.py                  # 全量离线回归（fixture + loopback，不碰真实账号）
bash dev/smoke_e2e.sh                     # 端到端冒烟，强制自建假 CLI
python3 dev/publish_pin.py <skill-dir>    # 目录树摘要，发布前后比对同一份字节
```

测试必须离线。`dev/offline_guard.py` 默认拒绝子进程，只放行精确核准的假 CLI 与采样 worker；不要为让测试通过而放宽它。

## 硬约束

- **不碰真实账号**：任何脚本测试只用 fixture / mock CLI。真实 PikPak、Telegram、凭据、网盘内容一律不读不写。
- **不自动删除**：所有 destructive 操作必须先出预览清单，由用户确认后执行。这是 14 个 skill 的共同设计前提。
- **发布包不含内部物**：只发布 `skills/pikpak-*`；不得包含快照、审计记录、凭据、`__pycache__`、`.pyc/.pyo/.pyd`。
- **不改测试门禁换绿灯**：不注释断言、不降阈值、不隐藏失败。测试失败先分辨是实现错误、测试错误还是环境问题。
- **邀请码 `22874035` 是有意公开的**，其余凭据、token、密码、连接字符串一律写 `[REDACTED]`。

## 目录约定

| 路径 | 作用 |
|---|---|
| `skills/pikpak-*/SKILL.md` | 单 skill 的触发词、边界、命令 |
| `skills/pikpak-*/references/upstream-design.md` | 该 skill 的上游设计依据 |
| `skills/pikpak-*/scripts/` | 只读优先的实现脚本 |
| `dev/` | 测试与发布工具，不随 skill 分发 |
| `INVITE-CODE.md` | 面向搜索流量的注册说明（含推广披露） |

## 当前状态

- 14 个 skill 全部为**实验状态**，生产推荐 `none`。真实账号闭环、官方 schema、配额、挂载、性能、断电恢复均未全面验收。
- 离线测试基线：`379 tests / 378 passed / 1 skipped / 0 fail`。
- ClawHub SkillSpector 分数是**风险分**（0 最安全），且同字节重复扫描不稳定，只作趋势参考，不作验收依据。
- 推广侧事实：活动归因口径为 `AFFILIATE_CODE`（CLI / agent 流程），37 个合格地区不含中国大陆、香港、澳门。注册量与 S$1 奖励不构成同一指标。

## 下一步

1. 补齐真实账号下的最小闭环验收（保存、下载、配额读取）。
2. 把 `publish_pin.py` 摘要纳入发布流程，替代按版本号核对。
3. 保持 SKILL.md 的触发词中英双语；正文以中文为主。
