<template>
  <div class="app-shell flex min-h-screen flex-col">
    <header class="app-header sticky top-0 z-40 border-b border-slate-200/80 bg-white/90 backdrop-blur-xl">
      <div class="mx-auto flex h-14 w-full max-w-6xl items-center px-4 sm:px-6 md:grid md:grid-cols-[auto_1fr_auto] md:gap-4">
        <NuxtLink to="/" class="flex items-center gap-2">
          <span class="inline-flex h-7 w-7 items-center justify-center rounded-lg bg-cyan-600/90 text-xs font-bold text-white">AI</span>
          <div class="leading-tight">
            <p class="m-0 font-display text-[11px] uppercase tracking-[0.18em] text-cyan-700">Patents</p>
            <p class="m-0 text-xs font-semibold text-slate-700">专利审查助手</p>
          </div>
        </NuxtLink>

        <nav class="hidden items-center justify-center gap-1 md:flex">
          <NuxtLink to="/#assistant" :class="navClass(route.path === '/')">首页</NuxtLink>
          <NuxtLink to="/tasks" :class="navClass(route.path.startsWith('/tasks'))">AI 任务</NuxtLink>
          <NuxtLink to="/account" :class="navClass(route.path.startsWith('/account'))">个人空间</NuxtLink>
          <NuxtLink to="/changelog" :class="navClass(route.path.startsWith('/changelog'))">更新日志</NuxtLink>
        </nav>

        <div class="ml-auto flex items-center gap-1.5 md:hidden">
          <NuxtLink to="/tasks" class="shrink-0 whitespace-nowrap rounded-full bg-cyan-600 px-3 py-1.5 text-xs font-semibold text-white shadow-sm shadow-cyan-200 transition hover:bg-cyan-700 md:hidden">
            AI任务
          </NuxtLink>
          <div class="relative md:hidden">
            <button
              type="button"
              class="inline-flex h-8 w-8 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-600 transition hover:bg-slate-50"
              aria-label="打开菜单"
              :aria-expanded="isMobileMenuOpen ? 'true' : 'false'"
              @click="toggleMobileMenu"
            >
              <svg viewBox="0 0 24 24" class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M4 7h16M4 12h16M4 17h16" stroke-linecap="round" />
              </svg>
            </button>

            <button
              v-if="isMobileMenuOpen"
              type="button"
              class="fixed inset-0 z-30"
              aria-label="关闭菜单"
              @click="closeMobileMenu"
            />
            <div
              v-if="isMobileMenuOpen"
              class="absolute right-0 top-[calc(100%+0.5rem)] z-40 w-36 rounded-xl border border-slate-200 bg-white p-1.5 shadow-lg shadow-slate-200/80"
            >
              <p
                v-if="hasAuthingEnabled && authStore.isLoggedIn"
                class="rounded-lg px-2.5 py-2 text-xs font-semibold text-slate-700"
              >
                欢迎，{{ displayUserName }}
              </p>
              <NuxtLink
                to="/account"
                class="block rounded-lg px-2.5 py-2 text-xs font-medium text-slate-700 transition hover:bg-slate-50"
                :class="hasAuthingEnabled && authStore.isLoggedIn ? 'mt-1 border-t border-slate-100 pt-2.5' : ''"
                @click="closeMobileMenu"
              >
                个人空间
              </NuxtLink>
              <button
                v-if="hasAuthingEnabled && authStore.isLoggedIn"
                type="button"
                class="mt-1 block w-full rounded-lg px-2.5 py-2 text-left text-xs font-medium text-slate-700 transition hover:bg-slate-50"
                @click="onMobileLogout"
              >
                退出登录
              </button>
              <button
                v-else-if="hasAuthingEnabled"
                type="button"
                class="mt-1 block w-full rounded-lg px-2.5 py-2 text-left text-xs font-medium text-slate-700 transition hover:bg-slate-50"
                @click="onMobileLogin"
              >
                登录
              </button>
            </div>
          </div>
        </div>

        <div class="hidden items-center justify-end gap-2 md:flex">
          <span
            v-if="hasAuthingEnabled && authStore.isLoggedIn"
            class="text-xs font-medium text-slate-600"
          >
            欢迎，{{ displayUserName }}
          </span>
          <button
            v-if="hasAuthingEnabled && authStore.isLoggedIn"
            type="button"
            class="shrink-0 whitespace-nowrap rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 transition hover:bg-slate-50"
            @click="onLogout"
          >
            退出
          </button>
          <button
            v-else-if="hasAuthingEnabled"
            type="button"
            class="shrink-0 whitespace-nowrap rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 transition hover:bg-slate-50"
            @click="onLogin"
          >
            登录
          </button>
        </div>
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
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useAuthStore } from '~/stores/auth'

const route = useRoute()
const config = useRuntimeConfig()
const authStore = useAuthStore()
const currentYear = new Date().getFullYear()
const serviceStatus = ref<string | null>(null)
const serviceVersion = ref<string | null>(null)
const isMobileMenuOpen = ref(false)
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
const displayUserName = computed(() => {
  const candidates = [
    authStore.user?.name,
    authStore.user?.nickname,
    authStore.user?.phone,
    authStore.user?.email,
  ]
  return candidates.find((item) => item && String(item).trim().length > 0) || '已登录用户'
})
const hasAuthingEnabled = computed(() => String(config.public.authingAppId || '').trim().length > 0)

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

const onLogin = async () => {
  await authStore.login()
}

const onLogout = async () => {
  await authStore.logout()
}

const closeMobileMenu = () => {
  isMobileMenuOpen.value = false
}

const toggleMobileMenu = () => {
  isMobileMenuOpen.value = !isMobileMenuOpen.value
}

const onMobileLogin = async () => {
  closeMobileMenu()
  await onLogin()
}

const onMobileLogout = async () => {
  closeMobileMenu()
  await onLogout()
}

watch(
  () => route.fullPath,
  () => {
    closeMobileMenu()
  },
)

onMounted(() => {
  if (hasAuthingEnabled.value) authStore.ensureInitialized()
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
