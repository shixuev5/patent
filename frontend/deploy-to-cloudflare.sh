#!/bin/bash

# Cloudflare Pages 部署脚本
# 使用方法: ./deploy-to-cloudflare.sh

set -e

echo "🚀 开始部署到 Cloudflare Pages..."

# 检查 Node.js 版本
if ! command -v node &> /dev/null; then
    echo "❌ 未安装 Node.js，请先安装 Node.js 18+"
    exit 1
fi

NODE_VERSION=$(node -v | cut -d'v' -f2 | cut -d'.' -f1)
if [ "$NODE_VERSION" -lt 18 ]; then
    echo "❌ Node.js 版本过低，需要 18+，当前版本: $(node -v)"
    exit 1
fi

echo "✅ Node.js 版本检查通过: $(node -v)"

# 安装依赖
echo "📦 安装依赖..."
npm install

# 构建项目
echo "🔨 构建项目..."
npm run generate

# 检查 wrangler
echo "☁️  检查 Cloudflare Wrangler..."
if ! command -v npx wrangler &> /dev/null; then
    echo "📥 安装 Wrangler..."
    npm install -g wrangler
fi

# 检查是否已登录
echo "🔑 检查 Cloudflare 登录状态..."
if ! npx wrangler whoami &> /dev/null; then
    echo "📝 请先登录 Cloudflare:"
    echo "   npx wrangler login"
    exit 1
fi

# 部署到 Cloudflare Pages
echo "🚀 部署到 Cloudflare Pages..."
npx wrangler pages deploy dist --project-name="patent-analyzer-frontend"

echo "✅ 部署完成！"
echo ""
echo "📝 提示:"
echo "   - 部署 URL: https://patent-analyzer-frontend.pages.dev"
echo "   - 如需自定义域名，请在 Cloudflare Dashboard 中配置"
echo "   - 生产环境请修改 API_BASE_URL 环境变量指向您的后端服务"

