<template>
  <div class="flex min-h-full flex-col space-y-5">
    <section class="rounded-3xl border border-slate-200 bg-white/92 p-6 shadow-sm shadow-slate-200 sm:p-8">
      <div>
        <p class="text-xs font-semibold uppercase tracking-[0.18em] text-cyan-700">Release Notes</p>
        <h1 class="mt-2 text-3xl font-semibold tracking-tight text-slate-900 sm:text-4xl">版本与更新日志</h1>
        <p class="mt-3 text-sm text-slate-600">
          当前运行版本：<strong class="text-cyan-700">v{{ serviceVersion || latestVersion }}</strong>
        </p>
      </div>
    </section>

    <section v-if="releases.length" class="space-y-4">
      <article
        v-for="release in releases"
        :key="release.version"
        class="rounded-2xl border border-slate-200 bg-white/95 p-5 shadow-sm shadow-slate-200 sm:p-6"
      >
        <div class="flex flex-wrap items-center justify-between gap-3">
          <h2 class="text-xl font-semibold text-slate-900">v{{ release.version }}</h2>
          <span class="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs text-slate-600">{{ release.date }}</span>
        </div>

        <p v-if="release.title" class="mt-3 text-sm text-slate-600">{{ release.title }}</p>

        <ul class="mt-4 space-y-2">
          <li
            v-for="item in release.items"
            :key="`${release.version}-${item.type}-${item.text}`"
            class="flex items-start gap-3 rounded-xl border border-slate-200 bg-slate-50/90 px-4 py-3"
          >
            <span class="inline-flex min-w-16 justify-center rounded-full px-2 py-0.5 text-xs font-semibold" :class="typeClass[item.type]">
              {{ typeLabel[item.type] }}
            </span>
            <span class="text-sm leading-6 text-slate-700">{{ item.text }}</span>
          </li>
        </ul>
      </article>
    </section>

    <p v-else class="rounded-2xl border border-slate-200 bg-white/95 px-5 py-6 text-sm text-slate-600 shadow-sm shadow-slate-200">
      暂无可展示的更新日志，请检查根目录 CHANGELOG.md 内容格式，确认版本标题与条目符合约定。
    </p>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import type { ChangelogApiResponse, ChangelogCategory, ChangelogRelease } from '~/types/changelog'
import { cachedGetJson } from '~/utils/apiClient'

const config = useRuntimeConfig()
const serviceVersion = ref<string | null>(null)
const releases = ref<ChangelogRelease[]>([])
const latestVersion = computed(() => releases.value[0]?.version || '--')

const typeLabel: Record<ChangelogCategory, string> = {
  feature: '新增',
  improvement: '优化',
  fix: '修复',
  breaking: '变更',
}

const typeClass: Record<ChangelogCategory, string> = {
  feature: 'border border-cyan-200 bg-cyan-50 text-cyan-700',
  improvement: 'border border-emerald-200 bg-emerald-50 text-emerald-700',
  fix: 'border border-amber-200 bg-amber-50 text-amber-700',
  breaking: 'border border-rose-200 bg-rose-50 text-rose-700',
}

const fetchHealthVersion = async () => {
  try {
    const data = await cachedGetJson<{ version?: string | null }>({
      baseUrl: config.public.apiBaseUrl,
      path: '/api/health',
      queryKey: ['public', 'health'],
      staleTime: 60 * 1000,
    })
    serviceVersion.value = data?.version ?? null
  } catch (error) {
    console.error('Failed to fetch health version:', error)
  }
}

const fetchChangelog = async () => {
  try {
    const data = await cachedGetJson<Partial<ChangelogApiResponse>>({
      baseUrl: config.public.apiBaseUrl,
      path: '/api/changelog',
      queryKey: ['public', 'changelog'],
      staleTime: 5 * 60 * 1000,
    })
    releases.value = Array.isArray(data.releases) ? data.releases : []
  } catch (error) {
    releases.value = []
    console.error('Failed to fetch changelog:', error)
  }
}

onMounted(() => {
  fetchHealthVersion()
  fetchChangelog()
})
</script>
