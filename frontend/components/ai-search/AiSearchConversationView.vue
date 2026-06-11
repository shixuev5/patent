<template>
  <AgentConversationView
    :session-id="sessionId"
    :entries="entries"
    :phase="phase"
    :attachments="attachments"
    :streaming="streaming"
    :phase-label="aiSearchPhaseLabel"
    empty-text="描述检索目标、技术方案、核心效果或约束条件。"
    @download-result="$emit('download-result', $event)"
  />
</template>

<script setup lang="ts">
import AgentConversationView from '~/components/agent/AgentConversationView.vue'
import type { AiSearchArtifactAttachment } from '~/types/aiSearch'
import { aiSearchPhaseLabel } from '~/utils/aiSearch'

withDefaults(defineProps<{
  sessionId?: string
  entries: Array<Record<string, any>>
  phase?: string
  attachments?: AiSearchArtifactAttachment[]
  streaming?: boolean
}>(), {
  sessionId: '',
  phase: '',
  attachments: () => [],
  streaming: false,
})

defineEmits<{
  'download-result': [attachment: AiSearchArtifactAttachment]
}>()
</script>
