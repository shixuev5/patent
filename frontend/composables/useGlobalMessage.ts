type GlobalMessageType = 'success' | 'error' | 'info'

type GlobalMessageState = {
  show: boolean
  type: GlobalMessageType
  text: string
}

let hideTimer: ReturnType<typeof setTimeout> | null = null

const createInitialState = (): GlobalMessageState => ({
  show: false,
  type: 'info',
  text: '',
})

export const useGlobalMessage = () => {
  const state = useState<GlobalMessageState>('global-message', createInitialState)

  const clearTimer = () => {
    if (!hideTimer) return
    clearTimeout(hideTimer)
    hideTimer = null
  }

  const closeMessage = () => {
    clearTimer()
    state.value = {
      ...state.value,
      show: false,
    }
  }

  const showMessage = (type: GlobalMessageType, text: string, duration = 4000) => {
    const normalized = String(text || '').trim()
    if (!normalized) return
    closeMessage()
    state.value = {
      show: true,
      type,
      text: normalized,
    }
    if (!import.meta.client || duration <= 0) return
    hideTimer = setTimeout(() => {
      state.value = {
        ...state.value,
        show: false,
      }
      hideTimer = null
    }, duration)
  }

  return {
    globalMessage: state,
    showMessage,
    closeMessage,
  }
}
