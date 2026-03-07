import type { GuardClient, GuardConstructor } from '~/types/authing-guard-cdn'

export default defineNuxtPlugin((nuxtApp) => {
  const config = useRuntimeConfig()
  const appId = String(config.public.authingAppId || '').trim()

  if (!appId) {
    return
  }

  const redirectUri = String(config.public.authingRedirectUri || '').trim()
  const host = String(config.public.authingDomain || '').trim()
  const guardFactory = (window as Window & { GuardFactory?: { Guard?: GuardConstructor } }).GuardFactory

  if (!guardFactory || typeof guardFactory.Guard !== 'function') {
    console.error('Authing Guard CDN 未加载，请检查 guard.min.js 是否已注入。')
    return
  }

  const guard = new guardFactory.Guard({
    appId,
    ...(redirectUri ? { redirectUri } : {}),
    ...(host ? { host } : {}),
  })

  nuxtApp.provide('guard', guard as GuardClient)
})
