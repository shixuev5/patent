import { createGuard } from '@authing/guard-vue3'
import '@authing/guard-vue3/dist/esm/guard.min.css'

export default defineNuxtPlugin((nuxtApp) => {
  const config = useRuntimeConfig()
  const appId = String(config.public.authingAppId || '').trim()

  if (!appId) {
    return
  }

  const redirectUri = String(config.public.authingRedirectUri || '').trim()
  const host = String(config.public.authingDomain || '').trim()

  const guard = createGuard({
    appId,
    ...(redirectUri ? { redirectUri } : {}),
    ...(host ? { host } : {}),
  })

  nuxtApp.vueApp.use(guard)
})
