<template>
  <div class="app-shell flex min-h-screen flex-col">
    <header class="app-header sticky top-0 z-40 border-b border-slate-200/80 bg-white/90 backdrop-blur-xl">
      <div class="mx-auto flex h-14 w-full max-w-6xl items-center justify-between px-4 sm:px-6">
        <NuxtLink to="/" class="flex items-center gap-2">
          <span class="inline-flex h-7 w-7 items-center justify-center rounded-lg bg-cyan-600/90 text-xs font-bold text-white">AI</span>
          <div class="leading-tight">
            <p class="font-display text-[11px] uppercase tracking-[0.18em] text-cyan-700">Patents</p>
            <p class="text-xs font-semibold text-slate-700">审查员智能助手</p>
          </div>
        </NuxtLink>

        <nav class="hidden items-center gap-1 md:flex">
          <NuxtLink to="/#assistant" :class="navClass(route.path === '/')">AI 助理</NuxtLink>
          <NuxtLink to="/tasks" :class="navClass(route.path.startsWith('/tasks'))">任务台</NuxtLink>
          <NuxtLink to="/changelog" :class="navClass(route.path.startsWith('/changelog'))">更新日志</NuxtLink>
        </nav>

        <NuxtLink to="/tasks" class="rounded-full bg-cyan-600 px-3.5 py-1.5 text-xs font-semibold text-white shadow-sm shadow-cyan-200 transition hover:bg-cyan-700">
          进入任务台
        </NuxtLink>
      </div>
    </header>

    <main class="mx-auto w-full max-w-6xl flex-1 px-4 pb-8 pt-5 sm:px-6 sm:pt-7">
      <slot />
    </main>

    <footer class="border-t border-slate-200/80 bg-white/70">
      <div class="mx-auto flex w-full max-w-6xl flex-wrap items-center justify-between gap-2 px-4 py-3 text-xs text-slate-500 sm:px-6">
        <p>Copyright © {{ currentYear }} aipatents.cn. All rights reserved.</p>
        <div class="flex flex-wrap items-center gap-3">
          <p class="inline-flex items-center gap-1.5">
            <span class="inline-block h-1.5 w-1.5 rounded-full" :class="isServiceHealthy ? 'bg-emerald-500' : 'bg-rose-500'" />
            服务{{ serviceStatusText }}
          </p>
          <p>版本 v{{ versionText }}</p>
        </div>
      </div>
    </footer>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

const route = useRoute()
const config = useRuntimeConfig()
const currentYear = new Date().getFullYear()
const serviceStatus = ref<string | null>(null)
const serviceVersion = ref<string | null>(null)
let healthTimer: ReturnType<typeof setInterval> | null = null

const isServiceHealthy = computed(() => {
  const text = (serviceStatus.value || '').trim().toLowerCase()
  if (!text) return false
  return (
    text.includes('正常')
    || text.includes('ok')
    || text.includes('healthy')
    || text.includes('running')
    || text.includes('up')
  )
})

const serviceStatusText = computed(() => (isServiceHealthy.value ? '正常' : '异常'))
const versionText = computed(() => serviceVersion.value || '--')

const navClass = (active: boolean) => {
  return active
    ? 'rounded-full bg-cyan-50 px-3 py-1.5 text-xs font-semibold text-cyan-700'
    : 'rounded-full px-3 py-1.5 text-xs font-medium text-slate-500 transition hover:bg-slate-100 hover:text-slate-800'
}

const fetchHealthStats = async () => {
  try {
    const response = await fetch(`${config.public.apiBaseUrl}/api/health`)
    if (!response.ok) throw new Error(`status: ${response.status}`)
    const data = await response.json()
    serviceStatus.value = typeof data?.status === 'string' ? data.status : null
    serviceVersion.value = data?.version ?? null
  } catch (error) {
    serviceStatus.value = null
    console.error('Failed to fetch health stats:', error)
  }
}

onMounted(() => {
  fetchHealthStats()
  healthTimer = setInterval(fetchHealthStats, 30000)
})

onBeforeUnmount(() => {
  if (healthTimer) clearInterval(healthTimer)
})
</script>

<style scoped>
.app-shell {
  background:
    radial-gradient(circle at 92% -8%, rgba(14, 165, 233, 0.16), transparent 32%),
    radial-gradient(circle at -8% 68%, rgba(45, 212, 191, 0.14), transparent 38%),
    linear-gradient(180deg, #f8fbff 0%, #f3f6fb 100%);
}

.font-display {
  font-family: 'Orbitron', 'Noto Sans SC', sans-serif;
}
</style>
