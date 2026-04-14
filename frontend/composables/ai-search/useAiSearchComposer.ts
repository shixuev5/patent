import { computed, ref } from 'vue'
import { isAiSearchExecutionPhase } from '~/utils/aiSearch'

export type ComposerMode = 'chat' | 'answer' | 'blocked'

export const useAiSearchComposer = ({
  currentSession,
  pendingQuestion,
  pendingConfirmation,
  resumeAction,
  humanDecisionAction,
  activePhase,
  storeInputDisabled,
  sendMessage,
  answerQuestion,
}: {
  currentSession: { value: Record<string, any> | null }
  pendingQuestion: { value: Record<string, any> | null }
  pendingConfirmation: { value: Record<string, any> | null }
  resumeAction: { value: Record<string, any> | null }
  humanDecisionAction: { value: Record<string, any> | null }
  activePhase: { value: string }
  storeInputDisabled: { value: boolean }
  sendMessage: (content: string) => Promise<void>
  answerQuestion: (questionId: string, content: string) => Promise<void>
}) => {
  const composer = ref('')

  const composerMode = computed<ComposerMode>(() => {
    if (resumeAction.value?.available || humanDecisionAction.value?.available) return 'blocked'
    if (pendingQuestion.value) return 'answer'
    return 'chat'
  })

  const inputDisabled = computed(() => storeInputDisabled.value || !currentSession.value || composerMode.value === 'blocked')
  const canSubmitMessage = computed(() => !!composer.value.trim() && !inputDisabled.value)

  const inputPlaceholder = computed(() => {
    if (!currentSession.value) return '正在准备会话...'
    if (pendingQuestion.value) return '请直接补充回答上面的问题'
    if (pendingConfirmation.value) return '可继续补充调整意见，或直接确认当前计划'
    if (resumeAction.value?.available) return '当前失败步骤需要先恢复执行。'
    if (humanDecisionAction.value?.available) return '当前处于人工决策状态，请选择继续检索或按当前结果完成。'
    if (isAiSearchExecutionPhase(activePhase.value)) {
      return '执行中补充的消息会在下一个执行节点统一生效'
    }
    return '继续调整检索计划'
  })

  const submitMessage = async () => {
    const content = composer.value.trim()
    if (!content) return
    if (resumeAction.value?.available) return
    if (humanDecisionAction.value?.available) return
    if (!currentSession.value) return
    composer.value = ''
    if (composerMode.value === 'answer') {
      const questionId = String(pendingQuestion.value?.question_id || pendingQuestion.value?.questionId || '').trim()
      if (!questionId) return
      await answerQuestion(questionId, content)
      return
    }
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
