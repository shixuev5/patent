import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRegisterSW } from 'virtual:pwa-register/vue'

type InstallActionResult = 'accepted' | 'dismissed' | 'ios-guide' | 'unsupported'

export const usePwa = () => {
  const deferredPrompt = ref<BeforeInstallPromptEvent | null>(null)
  const canInstall = computed(() => !!deferredPrompt.value)
  const isOffline = ref(false)
  const dismissedNeedRefresh = ref(false)
  const isStandalone = ref(false)
  const isIOS = computed(() => {
    if (!import.meta.client) return false
    const ua = navigator.userAgent || ''
    return /iphone|ipad|ipod/i.test(ua)
  })
  const canShowIOSGuide = computed(() => isIOS.value && !isStandalone.value)
  // Always show install entry when not in standalone mode.
  // `beforeinstallprompt` may not fire until engagement criteria are met.
  const showInstallEntry = computed(() => !isStandalone.value)

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

  const updateDisplayMode = () => {
    const standaloneByMedia = window.matchMedia('(display-mode: standalone)').matches
    const standaloneByNavigator = typeof (window.navigator as Navigator & { standalone?: boolean }).standalone === 'boolean'
      ? Boolean((window.navigator as Navigator & { standalone?: boolean }).standalone)
      : false
    isStandalone.value = standaloneByMedia || standaloneByNavigator
  }

  const onDisplayModeChange = () => {
    updateDisplayMode()
  }

  const installApp = async (): Promise<InstallActionResult> => {
    const promptEvent = deferredPrompt.value
    if (promptEvent) {
      await promptEvent.prompt()
      const result = await promptEvent.userChoice
      deferredPrompt.value = null
      return result.outcome === 'accepted' ? 'accepted' : 'dismissed'
    }

    if (canShowIOSGuide.value) return 'ios-guide'
    return 'unsupported'
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
    const mediaQuery = window.matchMedia('(display-mode: standalone)')
    updateNetworkState()
    updateDisplayMode()
    window.addEventListener('online', updateNetworkState)
    window.addEventListener('offline', updateNetworkState)
    window.addEventListener('beforeinstallprompt', onBeforeInstallPrompt)
    window.addEventListener('appinstalled', onAppInstalled)
    if (typeof mediaQuery.addEventListener === 'function') {
      mediaQuery.addEventListener('change', onDisplayModeChange)
    } else if (typeof mediaQuery.addListener === 'function') {
      mediaQuery.addListener(onDisplayModeChange)
    }
  })

  onBeforeUnmount(() => {
    const mediaQuery = window.matchMedia('(display-mode: standalone)')
    window.removeEventListener('online', updateNetworkState)
    window.removeEventListener('offline', updateNetworkState)
    window.removeEventListener('beforeinstallprompt', onBeforeInstallPrompt)
    window.removeEventListener('appinstalled', onAppInstalled)
    if (typeof mediaQuery.removeEventListener === 'function') {
      mediaQuery.removeEventListener('change', onDisplayModeChange)
    } else if (typeof mediaQuery.removeListener === 'function') {
      mediaQuery.removeListener(onDisplayModeChange)
    }
  })

  return {
    canInstall,
    showInstallEntry,
    installApp,
    needRefresh,
    applyUpdate,
    dismissUpdate,
    isOffline,
    isStandalone: computed(() => isStandalone.value),
    canShowIOSGuide,
    offlineReady: computed(() => onOfflineReady.value),
  }
}
