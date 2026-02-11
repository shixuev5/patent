// https://nuxt.com/docs/api/configuration/nuxt-config
export default defineNuxtConfig({
  devtools: { enabled: process.env.NODE_ENV === 'development' },
  
  modules: [
    '@pinia/nuxt',
  ],
  
  css: ['~/assets/css/main.css'],
  
  postcss: {
    plugins: {
      tailwindcss: {},
      autoprefixer: {},
    },
  },
  
  // 静态站点生成配置（Cloudflare Pages 优化）
  nitro: {
    preset: 'static',
    prerender: {
      routes: ['/'],
    },
  },
  
  // 运行时配置
  runtimeConfig: {
    public: {
      // API 基础URL - 生产环境需要配置为实际的后端地址
      apiBaseUrl: process.env.NUXT_PUBLIC_API_BASE_URL || 'http://localhost:8000',
    },
  },
  
  app: {
    head: {
      title: '专利智能分析平台',
      meta: [
        { charset: 'utf-8' },
        { name: 'viewport', content: 'width=device-width, initial-scale=1' },
        { name: 'description', content: 'AI驱动的专利智能分析平台，支持专利号查询和PDF分析，生成专业报告' },
        { name: 'theme-color', content: '#2563EB' },
      ],
      link: [
        { rel: 'preconnect', href: 'https://fonts.googleapis.com' },
        { rel: 'preconnect', href: 'https://fonts.gstatic.com', crossorigin: '' },
        { rel: 'stylesheet', href: 'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap' },
      ],
    },
  },
  
  // TypeScript 配置
  typescript: {
    strict: true,
    typeCheck: false,
  },
  
  // 优化配置
  experimental: {
    payloadExtraction: false,
  },
  
  // 构建配置
  build: {
    transpile: ['@heroicons/vue'],
  },
})
