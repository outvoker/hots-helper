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

## Space Secrets

连的 Supabase 项目写死在源码里（`sync_defaults.py`），所以在
Space → **Settings → Variables and secrets** 通常只需加一个：

| 名称 | 说明 |
|------|------|
| `HOTS_ACCESS_PASSWORD` | 全站访问口令（浏览器会弹出输入框，用户名随意） |

可选：`SUPABASE_URL` / `SUPABASE_ANON_KEY`（覆盖内置项目，私有部署才需要）、
`HOTS_REFRESH_SECONDS`（刷新间隔，默认 600 秒）。

启动时服务会从 Supabase 拉取全部对局到容器内的临时 SQLite，之后每
`HOTS_REFRESH_SECONDS` 秒增量刷新一次。服务**只读**，不会向云端写入。
