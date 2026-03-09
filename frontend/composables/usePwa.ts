import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRegisterSW } from 'virtual:pwa-register/vue'

export const usePwa = () => {
  const deferredPrompt = ref<BeforeInstallPromptEvent | null>(null)
  const canInstall = computed(() => !!deferredPrompt.value)
  const isOffline = ref(false)
  const dismissedNeedRefresh = ref(false)

  const onNeedRefresh = ref(false)
  const onOfflineReady = ref(false)
  let updateServiceWorker: ((reloadPage?: boolean) => Promise<void>) | null = null

  if (import.meta.client) {
    const sw = useRegisterSW({
      immediate: true,
      onRegisterError(error) {
        console.error('PWA service worker 注册失败：', error)
      },
    })
    updateServiceWorker = sw.updateServiceWorker

    watch(
      sw.needRefresh,
      (value) => {
        onNeedRefresh.value = value
        if (!value) dismissedNeedRefresh.value = false
      },
      { immediate: true },
    )

    watch(
      sw.offlineReady,
      (value) => {
        onOfflineReady.value = value
      },
      { immediate: true },
    )
  }

  const needRefresh = computed(() => onNeedRefresh.value && !dismissedNeedRefresh.value)

  const onBeforeInstallPrompt = (event: BeforeInstallPromptEvent) => {
    event.preventDefault()
    deferredPrompt.value = event
  }

  const onAppInstalled = () => {
    deferredPrompt.value = null
  }

  const updateNetworkState = () => {
    isOffline.value = !navigator.onLine
  }

  const installApp = async () => {
    const promptEvent = deferredPrompt.value
    if (!promptEvent) return false
    await promptEvent.prompt()
    const result = await promptEvent.userChoice
    if (result.outcome === 'accepted') deferredPrompt.value = null
    return result.outcome === 'accepted'
  }

  const applyUpdate = async () => {
    if (!updateServiceWorker) return
    dismissedNeedRefresh.value = false
    await updateServiceWorker(true)
  }

  const dismissUpdate = () => {
    dismissedNeedRefresh.value = true
  }

  onMounted(() => {
    updateNetworkState()
    window.addEventListener('online', updateNetworkState)
    window.addEventListener('offline', updateNetworkState)
    window.addEventListener('beforeinstallprompt', onBeforeInstallPrompt)
    window.addEventListener('appinstalled', onAppInstalled)
  })

  onBeforeUnmount(() => {
    window.removeEventListener('online', updateNetworkState)
    window.removeEventListener('offline', updateNetworkState)
    window.removeEventListener('beforeinstallprompt', onBeforeInstallPrompt)
    window.removeEventListener('appinstalled', onAppInstalled)
  })

  return {
    canInstall,
    installApp,
    needRefresh,
    applyUpdate,
    dismissUpdate,
    isOffline,
    offlineReady: computed(() => onOfflineReady.value),
  }
}
