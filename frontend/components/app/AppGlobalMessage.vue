<template>
  <transition name="toast">
    <div
      v-if="globalMessage.show"
      class="fixed left-1/2 top-16 z-50 flex max-w-[calc(100vw-2rem)] -translate-x-1/2 items-center gap-3 rounded-2xl border px-4 py-3 text-sm font-semibold shadow-lg backdrop-blur-md"
      :class="messageClass"
    >
      <span class="min-w-0 break-words">{{ globalMessage.text }}</span>
      <button
        type="button"
        class="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-current/15 bg-white/70 text-current transition hover:bg-white"
        aria-label="关闭提示"
        @click="closeMessage"
      >
        <XMarkIcon class="h-3.5 w-3.5" />
      </button>
    </div>
  </transition>
</template>

<script setup lang="ts">
import { XMarkIcon } from '@heroicons/vue/24/outline'
import { computed } from 'vue'

const { globalMessage, closeMessage } = useGlobalMessage()

const messageClass = computed(() => {
  if (globalMessage.value.type === 'success') {
    return 'border-emerald-200/70 bg-emerald-50/95 text-emerald-700 shadow-emerald-200/60'
  }
  if (globalMessage.value.type === 'error') {
    return 'border-rose-200/70 bg-rose-50/95 text-rose-700 shadow-rose-200/60'
  }
  return 'border-cyan-200/70 bg-cyan-50/95 text-cyan-700 shadow-cyan-200/60'
})
</script>

<style scoped>
.toast-enter-active,
.toast-leave-active {
  transition: opacity 0.22s ease, transform 0.22s ease;
}

.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translate(-50%, -8px);
}
</style>
