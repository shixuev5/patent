# Patent Analyzer Deployment Guide

本项目建议采用以下部署架构：

- 后端：Render（FastAPI）
- 前端：Cloudflare Pages（Nuxt3 静态站点）

## 1. 后端部署到 Render

仓库已提供 `render.yaml`，可直接用 Blueprint 部署。

1. 将代码推送到 GitHub/GitLab 仓库。
2. 在 Render 控制台选择 `New +` -> `Blueprint`，连接该仓库。
3. Render 会读取根目录 `render.yaml` 并创建 `patent-backend` 服务。
4. 在 Render 中补充 `sync: false` 的密钥类环境变量（如 `LLM_API_KEY`、`VLM_API_KEY`）。
5. 首次构建完成后，访问健康检查：
   - `https://<your-render-domain>/api/health`

说明：

- `render.yaml` 已配置持久化磁盘 `/var/data`。
- 后端使用 `APP_STORAGE_ROOT=/var/data/patent`，数据文件会写入该目录：
  - `data/tasks.db`
  - `uploads/`
  - `output/`

## 2. 前端部署到 Cloudflare Pages

前端目录：`frontend`

推荐使用 Cloudflare Pages Git 集成：

1. 在 Cloudflare Pages 创建项目并连接仓库。
2. 设置项目根目录（Root directory）为 `frontend`。
3. 构建设置：
   - Build command: `npm run generate`
   - Build output directory: `.output/public`
4. 添加环境变量：
   - `NUXT_PUBLIC_API_BASE_URL=https://<your-render-domain>`
5. 触发部署。

也可本地手动部署：

```bash
cd frontend
npm install
npx wrangler login
npm run deploy
```

## 3. 联调检查清单

1. 前端页面可正常打开并创建任务。
2. 浏览器网络请求目标为 Render 域名下的 `/api/tasks`。
3. SSE 接口可连通：`/api/tasks/{id}/progress`。
4. 任务完成后可下载 PDF。

## 4. 本地环境变量模板

后端 `.env.example` 新增了存储目录变量（可选）：

- `APP_STORAGE_ROOT`
- `APP_OUTPUT_DIR`
- `APP_DATA_DIR`
- `APP_UPLOAD_DIR`

## 5. 鉴权与每日配额

后端已启用用户级鉴权与每日分析上限：

- `POST /api/auth/guest`
  - 为前端创建匿名会话令牌。
- 受保护接口：
  - `POST /api/tasks`
  - `GET /api/tasks/{task_id}`
  - `GET /api/tasks/{task_id}/progress`
  - `GET /api/tasks/{task_id}/download`
  - `GET /api/usage`
- 令牌传递方式：
  - `Authorization: Bearer <token>`（推荐）
  - `?token=<token>`（用于 SSE/EventSource）
- 任务以 `owner_id` 做归属隔离，用户只能访问自己的任务。
- 每个用户每日默认最多提交 `3` 次分析。

新增环境变量：
- `AUTH_SECRET`（生产环境必填）
- `AUTH_TOKEN_TTL_DAYS`（默认：`30`）
- `MAX_DAILY_ANALYSIS`（默认：`3`）
- `APP_TZ_OFFSET_HOURS`（默认：`8`）
