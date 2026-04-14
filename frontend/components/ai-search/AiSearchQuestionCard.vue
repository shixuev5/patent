<template>
  <section class="rounded-2xl border border-amber-200 bg-amber-50/90 px-4 py-3">
    <div class="flex items-start justify-between gap-3">
      <div class="min-w-0">
        <p class="text-sm font-semibold text-amber-900">{{ title }}</p>
        <p class="mt-1 whitespace-pre-wrap text-sm leading-6 text-amber-900">{{ prompt }}</p>
      </div>
      <span
        v-if="pending"
        class="shrink-0 rounded-full border border-amber-300 bg-white/80 px-2.5 py-1 text-[11px] font-semibold text-amber-800"
      >
        待补充
      </span>
    </div>
    <p v-if="pending" class="mt-3 text-xs leading-5 text-amber-800">
      请直接在下方输入框补充回答。
    </p>
  </section>
</template>

<script setup lang="ts">
withDefaults(defineProps<{
  prompt: string
  title?: string
  pending?: boolean
}>(), {
  title: '待回答问题',
  pending: false,
})
</script>
