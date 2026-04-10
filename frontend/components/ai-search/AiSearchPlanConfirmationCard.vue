<template>
  <div class="mt-4 flex flex-wrap items-center justify-end gap-3 border-t border-slate-200 pt-3">
    <p class="text-xs text-slate-500">当前计划待确认，可继续在下方调整。</p>
    <div class="flex justify-end">
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
