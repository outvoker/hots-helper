---
title: HotS Helper
emoji: ⚔️
colorFrom: indigo
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
---

# HotS Helper — 战队数据网站

Heroes of the Storm 战队复盘数据的网页版。复用桌面端的纯数据分析层
（英雄强度、玩家战力、BP 建议、周报）并新增比赛记录浏览，数据从战队的
Supabase 项目实时同步。

这是部署在 **Hugging Face Spaces (Docker)** 的副本。源码与说明见
[`packaging/DEPLOY.md`](https://github.com/) 中的部署指南。

## 必需的 Space Secrets

在 Space → **Settings → Variables and secrets** 添加：

| 名称 | 说明 |
|------|------|
| `SUPABASE_URL` | 战队 Supabase 项目 URL（Project settings → API） |
| `SUPABASE_ANON_KEY` | 同页的 anon public key |
| `HOTS_ACCESS_PASSWORD` | 全站访问口令（浏览器会弹出输入框，用户名随意） |

可选：`HOTS_REFRESH_SECONDS`（云端数据刷新间隔，默认 600 秒）。

启动时服务会从 Supabase 拉取全部对局到容器内的临时 SQLite，之后每
`HOTS_REFRESH_SECONDS` 秒增量刷新一次。服务**只读**，不会向云端写入。
