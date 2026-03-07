// https://nuxt.com/docs/api/configuration/nuxt-config
console.log('Build API URL:', process.env.NUXT_PUBLIC_API_BASE_URL);

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
      routes: ['/', '/tasks', '/account', '/changelog', '/auth/callback'],
    },
  },
  
  // 运行时配置
  runtimeConfig: {
    public: {
      // API 基础URL - 生产环境需要配置为实际的后端地址
      apiBaseUrl: process.env.NUXT_PUBLIC_API_BASE_URL || 'http://localhost:8000',
      authingAppId: process.env.NUXT_PUBLIC_AUTHING_APP_ID || '',
      authingDomain: process.env.NUXT_PUBLIC_AUTHING_DOMAIN || '',
      authingRedirectUri: process.env.NUXT_PUBLIC_AUTHING_REDIRECT_URI || '',
    },
  },
  
  app: {
    head: {
      title: 'AI Patents - AI 专利审查助手',
      meta: [
        { charset: 'utf-8' },
        { name: 'viewport', content: 'width=device-width, initial-scale=1' },
        { name: 'description', content: 'aipatents.cn AI 专利审查助手，通过 AI 分析、AI 研判、AI 检索、AI 撰写协同，让审查员从 Pull 走向 Push，聚焦高价值方向与结果判断。' },
        { name: 'theme-color', content: '#2563EB' },
      ],
      link: [
        { rel: 'icon', type: 'image/svg+xml', href: '/favicon.svg' },
        { rel: 'shortcut icon', href: '/favicon.svg' },
        { rel: 'preconnect', href: 'https://fonts.loli.net' },
        { rel: 'stylesheet', href: 'https://fonts.loli.net/css2?family=Noto+Sans+SC:wght@400;500;600;700&family=Orbitron:wght@500;600;700&display=swap' },
        { rel: 'stylesheet', href: 'https://cdn.authing.co/packages/guard/latest/guard.min.css' },
      ],
      script: [
        { src: 'https://cdn.authing.co/packages/guard/latest/guard.min.js' },
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
