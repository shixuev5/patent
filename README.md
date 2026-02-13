# Patent Analyzer Deployment Guide

本项目支持以下部署架构：

## 部署选项

### 选项 1：Hugging Face Spaces（推荐免费部署）
- 后端：Hugging Face Spaces（Docker 容器）
- 前端：Cloudflare Pages（Nuxt3 静态站点）

## 1. 后端部署到 Hugging Face Spaces（推荐免费部署）

### 方案：Docker 容器部署

Hugging Face Spaces 提供了免费的 Docker 容器部署选项，支持 GPU 加速（部分计划）。

#### 部署步骤

1. **创建 Space**
   - 访问 [Hugging Face Spaces](https://huggingface.co/spaces)
   - 点击 "Create new Space"
   - 填写信息：
     - **Space name**: 输入您的空间名称（例如 `patent-analysis-system`）
     - **Visibility**: 选择 "Public"（免费版只能选择 Public）
     - **License**: 选择 "Apache 2.0" 或其他合适的许可证
     - **Space SDK**: 选择 "Docker"
   - 点击 "Create Space"

2. **上传代码**
   - 克隆您的代码仓库到本地
   - 进入仓库目录
   - 复制代码到 Space 目录：
     ```bash
     git clone https://huggingface.co/spaces/your-username/patent-analysis-system
     cd patent-analysis-system
     cp -r ../your-patent-repo/* .
     ```

3. **配置环境变量**
   在 Hugging Face Spaces 界面的 "Settings" -> "Variables" 中添加以下环境变量：

   ```bash
   # 基础配置
   PORT=7860
   APP_STORAGE_ROOT=/app

   # LLM 配置
   LLM_API_KEY=your_deepseek_api_key
   LLM_BASE_URL=https://api.deepseek.com
   LLM_MODEL=deepseek-chat
   LLM_MODEL_REASONING=deepseek-reasoner

   # 专利审查模型配置
   LLM_EXAM_API_KEY=your_siliconflow_api_key
   LLM_EXAM_BASE_URL=https://api.siliconflow.cn/v1
   LLM_MODEL_EXAM=deepseek-chat

   # VLM 配置
   VLM_API_KEY=your_baidu_api_key
   VLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
   VLM_MODEL=glm-4.6v

   # Mineru 配置
   MINERU_API_KEY=your_mineru_api_key
   MINERU_BASE_URL=https://mineru.net/api/v4
   MINERU_MODEL_SOURCE=modelscope

   # OCR 配置
   OCR_API_KEY=your_ocr_api_key
   OCR_BASE_URL=https://j9dd7babo5tcocz9.aistudio-app.com/ocr
   OCR_ENGINE=local

   # 智慧芽配置
   ZHIHUIYA_USERNAME=your_zhihuiya_username
   ZHIHUIYA_PASSWORD=your_zhihuiya_password

   # 存储配置
   TASK_STORAGE_BACKEND=d1
   R2_ENABLED=false

   # D1 数据库配置（Cloudflare D1）
   D1_ACCOUNT_ID=your_d1_account_id
   D1_DATABASE_ID=your_d1_database_id
   D1_API_TOKEN=your_d1_api_token
   D1_API_BASE_URL=https://api.cloudflare.com/client/v4

   # 认证配置
   AUTH_SECRET=your_auth_secret
   AUTH_TOKEN_TTL_DAYS=30

   # 配额限制
   MAX_DAILY_ANALYSIS=3
   APP_TZ_OFFSET_HOURS=8
   ```

4. **构建和部署**
   - 提交代码到 Hugging Face：
     ```bash
     git add .
     git commit -m "Initial commit"
     git push
     ```
   - Hugging Face Spaces 会自动构建和部署您的应用

5. **验证部署**
   - 访问 `https://your-username-patent-analysis-system.hf.space/api/health` 检查健康状态

### 注意事项

- **资源限制**：免费版有资源限制（CPU 1 核，RAM 8GB，存储 5GB，每日带宽 10GB）
- **数据持久化**：免费版的存储是临时的，重启后数据会丢失。如需持久存储，建议使用外部存储服务（如 Cloudflare R2）
- **端口配置**：Hugging Face Spaces 默认使用端口 7860
- **访问控制**：如果需要设置访问密码，可以使用 Hugging Face Spaces 的访问控制功能


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
