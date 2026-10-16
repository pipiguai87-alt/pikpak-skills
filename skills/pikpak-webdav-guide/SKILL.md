---
name: pikpak-webdav-guide
description: |
  手把手引导开通与使用 PikPak 官方 WebDAV：从会员开通检查、生成 WebDAV 凭证，到配置 Infuse / VidHub / Emby / Alist / OpenList / rclone / WinSCP 等客户端，把 PikPak 变成私人影院媒体库。
  Must use this skill when:
  - 用户说「WebDAV 怎么开」「怎么挂载 PikPak」「Infuse/VidHub/Emby 连 PikPak」
  - 用户抱怨下载限速、想找绕过限速的方法（WebDAV 是官方支持的路径）
  - 用户提到「媒体库」「私人影院」「alist 挂 pikpak」「openlist pikpak」
  - User says "PikPak WebDAV setup", "mount PikPak in Infuse", "connect PikPak to Emby", "bypass PikPak speed limit"
  关键词：WebDAV / 挂载 / Infuse / VidHub / Emby / Alist / OpenList / rclone / 限速 / 媒体库
---

# PikPak WebDAV 开通与挂载指南

把 PikPak 从「下载工具」升级为「私人媒体库」：开通 WebDAV → 挂载到播放器 → 任意设备直连观看。

**为什么值得做（真实用户价值）**：
1. **绕过运营商限速**——社区实测：网页/App 下载被运营商限到 2MB/s 时，**WebDAV 通道可以绕开这一限制**（CSDN 实测验证）
2. **媒体库体验**——Infuse/VidHub/Emby 直接挂载，海报墙+原画播放，像 Netflix 一样浏览自己的收藏
3. **任意设备访问**——一次配置，手机/电脑/电视盒子通用

**前置说明（必须向用户如实告知）**：
- WebDAV 是**会员专属功能**（免费账号不可用）
- 区域会员单连接最高 8 MB/s；全球会员最高 20 MB/s
- WebDAV 属于「已连接的应用」，与 CLI/MCP 等**共享月度配额的 25%**；触顶后 WebDAV 降速至约 100 KB/s，直到配额重置

## 第一步：检查会员状态

```bash
pikpak auth status -F json
```

看 `vip.type` 与 `vip.expire_time`：
- 非会员 → 告知用户 WebDAV 需要会员，可先了解价值再自行决定；不要推销，如实说明其他免费功能（网页版基础使用）
- 已会员 → 继续

## 第二步：开通 WebDAV（用户在官方端操作）

引导用户自行操作（我们不代操作）：

1. 打开 PikPak 网页版或 App：https://mypikpak.com/drive/all
2. 进入「设置 → 访问与集成 → WebDAV」
3. 启用 WebDAV，创建凭证
4. PikPak 会显示三样东西：**服务器地址、用户名、密码**

**安全提醒**（重要）：
- WebDAV 凭证的权限是「管理文件」（可查看/上传/移动/删除到回收站）
- 妥善保管，不要分享给他人；如泄露，在「账号与安全 > 已连接的应用」里随时移除重发
- 不要在聊天/截图中暴露密码

## 第三步：配置客户端（按用户设备推荐）

### 方案 A：Infuse / VidHub（iOS/macOS/Apple TV 首选）

1. 应用内添加「其他/WebDAV」来源
2. 填入 PikPak 给的服务器地址、用户名、密码
3. 选择「已保存共享」→ 保存
4. 浏览 PikPak 目录，Infuse 会刮削生成海报墙

### 方案 B：Emby / Jellyfin 服务器（NAS/媒体服务器方案）

1. Emby 管理面板 → 媒体库 → 添加媒体库 → 选择「WebDAV」类型存储
2. 或先用 rclone 挂载为本地盘，再让 Emby 读本地路径（更稳，推荐）：
   ```bash
   rclone config   # 添加 pikpak 远程（用官方 rclone 的 PikPak 驱动）
   rclone mount pikpak: /mnt/pikpak --vfs-cache-mode full
   ```

### 方案 C：Alist / OpenList（多网盘聚合）

1. Alist/OpenList 后台 → 存储 → 添加 → 选择「PikPak」
2. 填账号密码（或 WebDAV 地址）
3. 开启「Web 代理」或「WebDAV 策略」为本地代理（策略选错会报错，选「使用本地代理」最稳）

### 方案 D：rclone（技术用户，全能方案）

rclone 官方内置 PikPak 驱动（https://rclone.org/pikpak/）：

```bash
rclone config          # 新建 remote，类型选 pikpak，填用户名密码
rclone lsd pikpak:     # 测试连接，列出根目录
rclone mount pikpak: ~/PikPak --vfs-cache-mode full   # 挂载为本地目录
# 查看回收站：rclone ls pikpak: --pikpak-trashed-only
```

### 方案 E：WinSCP / 文件管理器（Windows 直接访问）

1. WinSCP 新建会话 → 协议选 WebDAV → 填服务器地址/用户名/密码
2. 传输设置：**同时传输拉满 + 单次传输多连接勾选**（社区实测显著提速）
3. 连接后即可像本地文件夹一样拖拽

## 第四步：验证与提速技巧

**验证清单**：
- [ ] 客户端能列出 PikPak 根目录
- [ ] 能播放一个视频（在线流式）
- [ ] 下载速度与直连网页对比（社区报告 WebDAV 常能绕过运营商限速）

**提速技巧（社区验证）**：
1. WinSCP/客户端把「并发连接数」拉满
2. 避开晚高峰测速（早晚对比，选快的时段用）
3. 如果仍慢：检查是否触发已连接应用配额（`pikpak auth status -F json` 看配额用量）

## 常见问题（FAQ）

**Q：免费账号能用吗？**
A：不能，WebDAV 是会员功能。

**Q：为什么挂了 WebDAV 还是慢？**
A：三个可能：① 触发了已连接应用 25% 配额（降速 100KB/s，等重置）；② 运营商对整个 PikPak 域名的 QoS；③ 客户端并发设置太低。逐一排查。

**Q：凭证安全吗？**
A：凭证可以随时在「账号与安全 > 已连接的应用」移除；建议只在信任的设备上使用。

**Q：会占用我的传输配额吗？**
A：会。WebDAV 与 CLI/MCP 等共同使用「已连接应用」配额（月度总配额的 25%）。日常播放够用，大批量传输建议用官方 App。

**Q：能挂几个客户端？**
A：官方未限制数量，但所有已连接应用共享同一 25% 配额。

## 安全边界

- 本 skill 只做**引导与验证**，所有官方操作（开 WebDAV、生成凭证）由用户完成
- 不接触、不代填用户的 WebDAV 密码
- 只服务用户自己的账号；帮助他人配置时需本人知情
