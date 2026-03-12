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
          <NuxtLink v-if="showAdminEntry" to="/admin/usage" :class="navClass(route.path.startsWith('/admin/usage'))">管理统计</NuxtLink>
          <NuxtLink to="/changelog" :class="navClass(route.path.startsWith('/changelog'))">更新日志</NuxtLink>
        </nav>

        <div class="ml-auto flex items-center gap-1.5 md:hidden">
          <NuxtLink to="/tasks" class="mobile-task-btn shrink-0 whitespace-nowrap rounded-full bg-cyan-600 px-3 py-1.5 text-xs font-semibold text-white shadow-sm shadow-cyan-200 transition hover:bg-cyan-700 md:hidden">
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
              <NuxtLink
                v-if="showAdminEntry"
                to="/admin/usage"
                class="mt-1 block rounded-lg px-2.5 py-2 text-xs font-medium text-slate-700 transition hover:bg-slate-50"
                @click="closeMobileMenu"
              >
                管理统计
              </NuxtLink>
              <button
                v-if="showInstallEntry"
                type="button"
                class="mt-1 block w-full rounded-lg border border-cyan-200 bg-cyan-50 px-2.5 py-2 text-left text-xs font-semibold text-cyan-700 transition hover:bg-cyan-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/70"
                @click="onInstallAppFromMobileMenu"
              >
                安装应用
              </button>
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
          <button
            v-if="showInstallEntry"
            type="button"
            class="shrink-0 whitespace-nowrap rounded-full border border-cyan-200 bg-cyan-50 px-3 py-1.5 text-xs font-semibold text-cyan-700 transition hover:bg-cyan-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/70"
            @click="onInstallApp"
          >
            安装应用
          </button>
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

    <div
      v-if="installHint"
      class="border-b border-sky-200/80 bg-sky-50/95"
    >
      <div class="mx-auto flex w-full max-w-6xl items-center gap-2 px-4 py-2.5 text-xs text-sky-800 sm:px-6">
        <p class="font-medium">{{ installHint }}</p>
        <button
          type="button"
          class="ml-auto rounded-full border border-sky-300 bg-white px-2.5 py-1 text-[11px] font-medium text-sky-700 transition hover:bg-sky-100"
          @click="installHint = ''"
        >
          知道了
        </button>
      </div>
    </div>

    <div
      v-if="needRefresh"
      class="border-b border-amber-200/80 bg-amber-50/95"
    >
      <div class="mx-auto flex w-full max-w-6xl flex-wrap items-center gap-2 px-4 py-2.5 text-xs text-amber-800 sm:px-6">
        <p class="font-medium">检测到新版本，刷新后可使用最新内容。</p>
        <div class="ml-auto flex items-center gap-2">
          <button
            type="button"
            class="rounded-full bg-amber-600 px-2.5 py-1 text-[11px] font-semibold text-white transition hover:bg-amber-700"
            @click="onApplyUpdate"
          >
            立即更新
          </button>
          <button
            type="button"
            class="rounded-full border border-amber-300 bg-white px-2.5 py-1 text-[11px] font-medium text-amber-700 transition hover:bg-amber-100"
            @click="dismissUpdate"
          >
            稍后
          </button>
        </div>
      </div>
    </div>

    <div
      v-if="isOffline"
      class="border-b border-slate-200/80 bg-slate-100/95"
    >
      <div class="mx-auto w-full max-w-6xl px-4 py-2 text-xs text-slate-600 sm:px-6">
        当前处于离线状态：页面壳层可访问，在线任务能力将在网络恢复后可用。
      </div>
    </div>

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
import { useAdminUsageStore } from '~/stores/adminUsage'
import { cachedGetJson } from '~/utils/apiClient'

const route = useRoute()
const config = useRuntimeConfig()
const authStore = useAuthStore()
const adminUsageStore = useAdminUsageStore()
const { showInstallEntry, installApp, needRefresh, applyUpdate, dismissUpdate, isOffline, canShowIOSGuide } = usePwa()
const currentYear = new Date().getFullYear()
const serviceStatus = ref<string | null>(null)
const serviceVersion = ref<string | null>(null)
const isMobileMenuOpen = ref(false)
const installHint = ref('')
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
const showAdminEntry = computed(() => adminUsageStore.isAdmin)

const navClass = (active: boolean) => {
  return active
    ? 'rounded-full bg-cyan-50 px-3 py-1.5 text-xs font-semibold text-cyan-700'
    : 'rounded-full px-3 py-1.5 text-xs font-medium text-slate-500 transition hover:bg-slate-100 hover:text-slate-800'
}

const fetchHealthStats = async () => {
  try {
    const data = await cachedGetJson<any>({
      baseUrl: config.public.apiBaseUrl,
      path: '/api/health',
      queryKey: ['public', 'health'],
      staleTime: 60 * 1000,
    })
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

const onInstallApp = async () => {
  try {
    const result = await installApp()
    if (result === 'ios-guide') {
      installHint.value = 'iOS Safari 暂不支持弹窗安装：请点击浏览器“分享”按钮，再选择“添加到主屏幕”。'
      return
    }
    if (result === 'unsupported') {
      installHint.value = '当前会话暂不可触发安装。请稍后重试或使用浏览器菜单安装。'
      return
    }
    if (result === 'dismissed') {
      installHint.value = canShowIOSGuide.value
        ? '安装未完成。iOS 可通过“分享 -> 添加到主屏幕”手动安装。'
        : '安装未完成。你可以稍后再次点击安装应用。'
      return
    }
    installHint.value = ''
  } catch (error) {
    console.error('安装应用失败：', error)
    installHint.value = '安装流程未完成，请稍后重试。'
  }
}

const onInstallAppFromMobileMenu = async () => {
  await onInstallApp()
  closeMobileMenu()
}

const onApplyUpdate = async () => {
  try {
    await applyUpdate()
  } catch (error) {
    console.error('应用更新失败：', error)
  }
}

const refreshAdminAccess = async (force = false) => {
  if (!hasAuthingEnabled.value) {
    adminUsageStore.isAdmin = false
    adminUsageStore.checkedAccess = true
    return
  }
  await adminUsageStore.fetchAccess(force)
}

watch(
  () => route.fullPath,
  () => {
    closeMobileMenu()
    installHint.value = ''
  },
)

onMounted(async () => {
  if (hasAuthingEnabled.value) await authStore.ensureInitialized()
  await refreshAdminAccess(false)
  fetchHealthStats()
  healthTimer = setInterval(fetchHealthStats, 30000)
})

watch(
  () => authStore.isLoggedIn,
  () => {
    void refreshAdminAccess(true)
  },
)

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

.mobile-task-btn,
.mobile-task-btn:link,
.mobile-task-btn:visited,
.mobile-task-btn:hover,
.mobile-task-btn:active {
  color: white !important;
}
</style>
