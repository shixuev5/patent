import type { GuardClient, GuardWindow } from '~/types/authing-guard-cdn'

export default defineNuxtPlugin((nuxtApp) => {
  const config = useRuntimeConfig()
  const appId = String(config.public.authingAppId || '').trim()

  if (!appId) {
    return
  }

  const redirectUri = String(config.public.authingRedirectUri || '').trim()
  const host = String(config.public.authingDomain || '').trim()
  const win = window as GuardWindow
  const Guard = win.Guard || win.GuardFactory?.Guard

  if (typeof Guard !== 'function') {
    return
  }

  const guard = new Guard({
    appId,
    ...(redirectUri ? { redirectUri } : {}),
    ...(host ? { host } : {}),
  })

  nuxtApp.provide('guard', guard as GuardClient)
})
