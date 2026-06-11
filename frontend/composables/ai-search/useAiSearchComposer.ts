import { computed, ref } from 'vue'

export type ComposerMode = 'chat' | 'blocked'

export const useAiSearchComposer = ({
  currentSession,
  activePhase,
  storeInputDisabled,
  sendMessage,
}: {
  currentSession: { value: Record<string, any> | null }
  activePhase: { value: string }
  storeInputDisabled: { value: boolean }
  sendMessage: (content: string) => Promise<void>
}) => {
  const composer = ref('')

  const composerMode = computed<ComposerMode>(() => 'chat')

  const inputDisabled = computed(() => storeInputDisabled.value || !currentSession.value)
  const canSubmitMessage = computed(() => !!composer.value.trim() && !inputDisabled.value)

  const inputPlaceholder = computed(() => {
    if (!currentSession.value) return '正在准备会话...'
    if (activePhase.value === 'running') {
      return '本轮检索进行中'
    }
    return '描述检索目标、停止条件，或让 agent 基于已有信息继续检索'
  })

  const submitMessage = async () => {
    const content = composer.value.trim()
    if (!content) return
    if (!currentSession.value) return
    composer.value = ''
    await sendMessage(content)
  }

  const onComposerEnter = (event: KeyboardEvent) => {
    if (event.isComposing || inputDisabled.value || !currentSession.value || !composer.value.trim()) return
    void submitMessage()
  }

  return {
    composer,
    composerMode,
    inputDisabled,
    canSubmitMessage,
    inputPlaceholder,
    submitMessage,
    onComposerEnter,
  }
}
