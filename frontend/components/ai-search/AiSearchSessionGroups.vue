<template>
  <div class="space-y-3">
    <section
      v-for="group in groups"
      :key="group.key"
      class="space-y-2"
    >
      <p class="px-1 text-[11px] font-semibold tracking-[0.18em] text-slate-400">{{ group.label }}</p>
      <AiSearchSessionListItem
        v-for="session in group.items"
        :key="session.sessionId"
        :session="session"
        :active="session.sessionId === activeSessionId"
        :busy="isBusy(session.sessionId)"
        @select="$emit('select', $event)"
        @rename="$emit('rename', $event.sessionId, $event.title)"
        @toggle-pin="$emit('toggle-pin', $event.sessionId, $event.pinned)"
        @delete="$emit('delete', $event)"
      />
    </section>
  </div>
</template>

<script setup lang="ts">
import AiSearchSessionListItem from '~/components/ai-search/AiSearchSessionListItem.vue'
import type { AiSearchSessionSummary } from '~/types/aiSearch'

type SessionGroup = {
  key: string
  label: string
  items: AiSearchSessionSummary[]
}

const props = defineProps<{
  groups: SessionGroup[]
  activeSessionId?: string
  isBusy?: (sessionId: string) => boolean
}>()

defineEmits<{
  select: [sessionId: string]
  rename: [sessionId: string, title: string]
  'toggle-pin': [sessionId: string, pinned: boolean]
  delete: [sessionId: string]
}>()

const isBusy = (sessionId: string): boolean => {
  return typeof props.isBusy === 'function' ? !!props.isBusy(sessionId) : false
}
</script>
