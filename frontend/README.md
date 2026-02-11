# 专利智能分析平台 - 前端部署指南

## 项目简介

基于 Vue 3 + Nuxt 3 + TypeScript 构建的专利智能分析平台前端，采用极简单列布局设计，支持：
- 输入专利号或上传 PDF 文件
- 实时任务进度追踪
- 任务历史持久化（localStorage）
- 结果下载功能

## 技术栈

- **框架**: Vue 3 + Nuxt 3
- **语言**: TypeScript
- **样式**: Tailwind CSS
- **状态管理**: Pinia
- **图标**: Heroicons
- **部署**: Cloudflare Pages

## 快速开始

### 1. 安装依赖

```bash
cd patent-frontend
npm install
```

### 2. 开发模式

```bash
npm run dev
```

访问 http://localhost:3000

### 3. 构建生产版本

```bash
npm run generate
```

## Cloudflare Pages 部署

### 方法一：自动部署脚本

```bash
# 确保已安装 Wrangler 并登录
npx wrangler login

# 运行部署脚本
./deploy-to-cloudflare.sh
```

### 方法二：手动部署

1. **构建项目**:
   ```bash
   npm run generate
   ```

2. **部署到 Cloudflare Pages**:
   ```bash
   npx wrangler pages deploy .output/public --project-name="patent-analyzer-frontend"
   ```

3. **配置环境变量** (生产环境):
   
   在 Cloudflare Dashboard → Pages → 项目设置 → 环境变量中添加：
   
   ```
   NUXT_PUBLIC_API_BASE_URL=https://your-backend-api.com
   ```

### 方法三：Git 集成自动部署

1. 将代码推送到 GitHub/GitLab
2. 在 Cloudflare Dashboard 中创建 Pages 项目
3. 连接 Git 仓库
4. 构建设置：
   - Build command: `npm run generate`
   - Build output directory: `.output/public`

## 项目结构

```
frontend/
├── components/
│   ├── sections/          # 页面区块组件
│   ├── task/              # 任务相关组件
│   ├── upload/            # 上传相关组件
│   └── ui/                # 基础UI组件
├── composables/           # 组合式函数
├── layouts/               # 布局组件
├── pages/                 # 页面
├── plugins/               # 插件
├── public/                # 静态资源
├── stores/                # Pinia 状态管理
├── types/                 # TypeScript 类型
└── nuxt.config.ts         # Nuxt 配置
```

## 配置说明

### 后端 API 地址

开发环境：`.env` 文件
```
NUXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

生产环境：Cloudflare Pages 环境变量
```
NUXT_PUBLIC_API_BASE_URL=https://your-production-api.com
```

### 支持的 API 端点

- `POST /api/tasks` - 创建任务（支持 patentNumber 或 file）
- `GET /api/tasks/:id/progress` - SSE 实时进度流
- `GET /api/tasks/:id/download` - 下载结果文件

## 开发注意事项

1. **localStorage 仅在客户端可用**：使用 `process.client` 检查
2. **SSE 连接自动重连**：断线后 3 秒自动重试
3. **文件大小限制**：50MB，仅支持 PDF
4. **任务状态持久化**：刷新页面后自动恢复

## 故障排除

### 部署失败
- 检查 Node.js 版本 >= 18
- 确保 `npm run generate` 能本地成功构建
- 检查 `.output/public` 目录是否生成

### API 连接失败
- 检查 `NUXT_PUBLIC_API_BASE_URL` 环境变量
- 确保后端服务允许 CORS
- 检查网络连通性

### 任务进度不更新
- 检查浏览器控制台网络请求
- 确保 SSE 端点正常工作
- 检查是否有代理阻止 SSE

## License

MIT
