<template>
  <div class="mt-4 rounded-2xl border border-cyan-200 bg-cyan-50/80 px-4 py-3">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <p class="text-sm font-semibold text-cyan-950">当前计划待确认</p>
        <p class="mt-1 text-xs leading-5 text-cyan-800">可以继续在下方输入框中修改计划，确认后将按该方向开始实施。</p>
      </div>
      <button
        type="button"
        class="rounded-xl bg-cyan-700 px-4 py-2 text-sm font-semibold text-white transition hover:bg-cyan-800 disabled:cursor-not-allowed disabled:bg-slate-300"
        :disabled="confirmDisabled"
        @click="$emit('confirm')"
      >
        {{ label }}
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  confirmDisabled: boolean
  label?: string
}>()

const emit = defineEmits<{
  confirm: []
}>()
</script>
