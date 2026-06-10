# 部署网页服务

网页服务（`hots-web`）把桌面端的数据分析层做成一个 FastAPI 后端 +
React 单页前端，数据从战队的 Supabase 项目实时同步。本服务**只读**，
不接触 OCR、翻译、replay 监听等桌面功能。

## 架构

```
浏览器  ──HTTP──>  FastAPI (hots-web, :7860)
                      │  启动 + 定时
                      ▼
                  CloudSync.sync_now()  ──REST──>  Supabase (Postgres)
                      │
                      ▼
                  临时 SQLite  ──>  现有分析层 (bp / player_rank / lookup / weekly)
```

前端静态文件由同一个 FastAPI 进程在 `/` 下托管；所有数据接口在 `/api/*`。

## 环境变量

| 变量 | 必需 | 说明 |
|------|------|------|
| `SUPABASE_URL` | 是* | Supabase 项目 URL |
| `SUPABASE_ANON_KEY` | 是* | Supabase anon public key |
| `HOTS_ACCESS_PASSWORD` | 建议 | 全站访问口令（HTTP Basic，用户名任意）。不设则公开访问 |
| `HOTS_REFRESH_SECONDS` | 否 | 云端刷新间隔，默认 600 |
| `HOTS_DB_PATH` | 否 | 直接打开某个本地 SQLite 快照，跳过云同步（与 Supabase 二选一） |
| `HOTS_WEB_PORT` / `PORT` | 否 | 监听端口，默认 7860 |

\* 不设 Supabase 也能启动，只是没有数据（接口返回空、前端显示「暂无数据」）。

## 方案一：Render（免费，推荐）

> 免费 Web Service，`*.onrender.com` 域名大陆多数能直连（偏慢）。免费层
> 闲置 15 分钟会休眠，下次访问冷启动数十秒（会重新全量拉云端数据）。

仓库根的 [`render.yaml`](../render.yaml) 已声明用 `Dockerfile` 构建，
所以最省事的方式是 **Blueprint**：

1. https://render.com 用 GitHub 登录。
2. **New → Blueprint**，选本仓库 → Render 读取 `render.yaml` 自动建好服务。
3. 首次部署会提示填环境变量（`render.yaml` 里标了 `sync: false` 的三个）：
   - `SUPABASE_URL`
   - `SUPABASE_ANON_KEY`
   - `HOTS_ACCESS_PASSWORD`
4. 等构建完成，打开 `https://<服务名>.onrender.com`，输入口令即可。

不想用 Blueprint 也可以手动：**New → Web Service → 选 Docker → 关联仓库**，
然后在 **Environment** 里填上面三个变量。Render 会注入 `PORT`，`hots-web`
已兼容；端口/启动命令都不用手动设。

> 容器文件系统是临时的，watermark 每次重新部署/休眠唤醒会重置 → 冷启动
> 重新全量拉云端数据（小队库很小，秒级，可接受）。
>
> 想避免休眠：可在 Render 升级到付费实例，或用一个外部定时 ping（如
> cron-job.org 每 10 分钟请求 `/api/health`）保活——注意这会持续消耗免费额度。

## 方案二：Railway（大陆可达性较好，按量计费）

> Railway 已无永久免费层：新账号有一次性 $5 试用额度，用完需 Hobby 方案
> （$5/月起，按用量计）。`*.up.railway.app` 域名在大陆通常能直连。

1. https://railway.app 用 GitHub 登录 → **New Project → Deploy from GitHub repo**，
   选本仓库。仓库根的 [`railway.json`](../railway.json) 已声明用 `Dockerfile`
   构建、启动命令 `hots-web`。
2. **Variables** 里填 `SUPABASE_URL`、`SUPABASE_ANON_KEY`、`HOTS_ACCESS_PASSWORD`。
3. **Settings → Networking → Generate Domain** 生成公网域名。

## 方案三：Hugging Face Spaces（永久免费，但大陆常被墙）

1. 新建一个 **Docker** 类型的 Space。
2. 把本仓库推上去（或在 Space 里关联 GitHub 仓库）。Space 根目录需要
   有一个带 front-matter 的 `README.md` —— 用
   [`packaging/huggingface/README.md`](huggingface/README.md) 的内容（`app_port: 7860`）。
3. 仓库根的 `Dockerfile` 会被自动用于构建（多阶段：先构建 SPA，再装
   精简版 Python 运行时，镜像约 240MB）。
4. 在 **Settings → Variables and secrets** 填入 `SUPABASE_URL`、
   `SUPABASE_ANON_KEY`、`HOTS_ACCESS_PASSWORD`。
5. 等待构建完成，打开 Space 域名，输入口令即可。

> 冷启动会从 Supabase 全量拉取一次（小队数据量很小，秒级）。容器文件
> 系统是临时的，watermark 每次重建都会重置，所以每次重新部署都会重新
> 全量拉 —— 对 <500MB 的库完全可接受。

## 方案四：Fly.io

`fly launch`（沿用本仓库 Dockerfile），`fly secrets set SUPABASE_URL=… SUPABASE_ANON_KEY=… HOTS_ACCESS_PASSWORD=…`。
可挂一个小卷把临时 SQLite + watermark 持久化，避免每次冷启动全量拉。

## 本地运行

```bash
# 开发：后端
pip install -e ".[web]"
SUPABASE_URL=… SUPABASE_ANON_KEY=… hots-web        # :7860
# 或用本地已有的 SQLite：
HOTS_DB_PATH=~/Library/Application\ Support/hots-helper/hots.db hots-web

# 开发：前端（带热更新，自动代理 /api 到 :7860）
cd web && npm install && npm run dev               # :5173

# 构建前端到后端静态目录（生产形态）
cd web && npm run build                            # 输出到 src/hots_helper/web/static
```

## 本地 Docker

```bash
docker build -t hots-helper-web .
docker run -p 7860:7860 \
  -e SUPABASE_URL=… -e SUPABASE_ANON_KEY=… -e HOTS_ACCESS_PASSWORD=… \
  hots-helper-web
```

## 测试

```bash
pytest --cov=src/hots_helper/web --cov=src/hots_helper/db --cov-report=term-missing
```
