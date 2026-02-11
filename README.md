# Patent Analyzer Deployment Guide

本项目建议采用以下部署架构：

- 后端：Render（FastAPI）
- 前端：Cloudflare Pages（Nuxt3 静态站点）

## 1. 后端部署到 Render

### 方案 A：免费计划（Hobby）手动部署（不支持 Blueprint）

Render 免费计划不能通过 `New +` -> `Blueprint` 从 `render.yaml` 一键创建服务，请改用手动创建 `Web Service`：
（参考：<https://render.com/docs/free>）

1. 将代码推送到 GitHub/GitLab 仓库。
2. 在 Render 控制台选择 `New +` -> `Web Service`，连接该仓库。
3. 按以下配置填写（与 `render.yaml` 保持一致）：
   - Runtime: `Python`
   - Build Command:
     ```bash
     pip install --upgrade pip uv
     uv sync --frozen --no-dev
     uv run playwright install chromium
     ```
   - Start Command: `uv run --no-sync uvicorn api:app --host 0.0.0.0 --port $PORT`
   - Health Check Path: `/api/health`
4. 在 Render 中补充环境变量（尤其是密钥类变量，如 `LLM_API_KEY`、`VLM_API_KEY`、`AUTH_SECRET`）。
   - 推荐同时配置持久化外部存储：
     - `TASK_STORAGE_BACKEND=d1` + `D1_ACCOUNT_ID` + `D1_DATABASE_ID` + `D1_API_TOKEN`
     - `R2_ENABLED=true` + `R2_*`（Cloudflare R2，用于 PDF/上传文件）
5. 首次构建完成后，访问健康检查：
   - `https://<your-render-domain>/api/health`

免费计划注意事项：

- 免费实例无持久化磁盘，容器重启/重建后本地文件会丢失。
- 如需长期保存 `tasks.db`、`uploads/`、`output/`，请升级到付费计划并挂载磁盘，或改用外部存储。

### 方案 B：付费计划（Starter 及以上）使用 Blueprint

仓库根目录已提供 `render.yaml`，付费计划可直接用 Blueprint 部署：

1. 在 Render 控制台选择 `New +` -> `Blueprint`，连接该仓库。
2. Render 会读取根目录 `render.yaml` 并创建 `patent-backend` 服务。
3. Blueprint 默认配置包含持久化磁盘 `/var/data`，并使用 `APP_STORAGE_ROOT=/var/data/patent` 存储业务数据。

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
- `TASK_STORAGE_BACKEND`（`sqlite` 用于本地调试，`d1` 用于线上）
- `D1_ACCOUNT_ID`
- `D1_DATABASE_ID`
- `D1_API_TOKEN`
- `D1_API_BASE_URL`
- `R2_ENABLED`
- `R2_ENDPOINT_URL`
- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`
- `R2_BUCKET`
- `R2_REGION`
- `R2_KEY_PREFIX`
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
