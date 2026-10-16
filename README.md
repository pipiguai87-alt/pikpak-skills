# pikpak-skills

给 AI Agent（Claude Code / Codex / Cursor / Hermes 等）用的 PikPak 云盘工具包。

三个技能，覆盖「上手 → 维护」的真实工作流：

| Skill | 用途 |
|-------|------|
| **pikpak-onboarding** | 手把手带新用户从零用上 PikPak：安装 CLI → 注册/登录 → 第一条磁力云下载 → 在线播放 |
| **pikpak-ad-cleaner** | 扫描资源包里夹带的广告/推广垃圾文件（.url 快捷方式、推广文本、二维码图、宣传短片），分级报告后确认清理 |
| **pikpak-dedup** | 同一资源的多个版本（不同清晰度/压制版）查重分组，按策略保留最优、清理旧版 |

## 设计原则

- **只读先行**：清理与查重工具只执行 `pikpak ls` 扫描（源码级保证，含回归测试），绝不静默删除。
- **用户确认**：所有清理都必须经过用户过目清单并明确确认。
- **可恢复**：删除一律走 `pikpak rm`（移入回收站，可恢复），不做永久销毁。
- **内容中立**：工具只处理文件名/元数据，不涉及资源内容本身。

## 安装

先安装 PikPak CLI（三个 skill 的共同前置）：

```bash
# macOS / Linux
curl -fsSL https://download.mypikpak.com/cli/install.sh | bash
# Windows PowerShell
irm https://download.mypikpak.com/cli/install.ps1 | iex
# Homebrew
brew install pikcloud/tap/pikpak-cli
```

再安装技能（以 skills CLI 为例）：

```bash
npx skills add pipiguai87-alt/pikpak-skills --skill pikpak-onboarding
npx skills add pipiguai87-alt/pikpak-skills --skill pikpak-ad-cleaner
npx skills add pipiguai87-alt/pikpak-skills --skill pikpak-dedup
```

## 关于邀请码

`pikpak-onboarding` 的 `config.json` 中带有一个邀请码（`affiliate_code`）。按照 PikPak 官方规则，技能在注册前**必须向用户披露**该邀请码及其归属，用户有权拒绝并使用 `pikpak auth register --affiliate=` 跳过。不进行任何用户不知情的注册。

如不需要邀请码，把 `config.json` 中的 `affiliate_code` 留空即可，技能会走无码注册流程。

## 开发

```bash
python3 dev/run_tests.py    # 13 项回归测试，全部离线（中性 fixture，不访问网络）
```

测试覆盖：广告三级分类规则、多页目录遍历、查重归一化（语义 token 保留）、保留策略选择、以及「脚本只读」的 AST 级安全断言。

## 边界

- 只用于用户有权管理的自有云盘内容；
- 不帮助获取、整理、传播盗版或违法内容；
- 遇到涉嫌违法的内容（如非自愿拍摄材料）一律拒绝服务。

## License

MIT
