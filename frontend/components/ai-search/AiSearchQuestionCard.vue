<template>
  <section class="rounded-2xl bg-amber-50/75 px-4 py-3">
    <div class="min-w-0">
      <p class="text-[13px] font-semibold text-amber-900">{{ title }}</p>
      <p class="mt-1 whitespace-pre-wrap text-[13px] leading-6 text-amber-900">{{ prompt }}</p>
    </div>
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
