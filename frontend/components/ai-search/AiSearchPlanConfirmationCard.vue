<template>
  <div class="mt-2 flex justify-end">
    <button
      type="button"
      class="inline-flex items-center justify-center rounded-lg bg-cyan-700 px-3 py-1.5 text-[12px] font-semibold text-white transition hover:bg-cyan-800 disabled:cursor-not-allowed disabled:bg-slate-300"
      :disabled="confirmDisabled"
      @click="$emit('confirm')"
    >
      {{ label }}
    </button>
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
