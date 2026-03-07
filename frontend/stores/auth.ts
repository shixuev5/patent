import { defineStore } from 'pinia'
import type { GuardClient, GuardConstructor } from '~/types/authing-guard-cdn'
import type { AuthState, AuthingUser } from '~/types/auth'

const TASK_AUTH_TOKEN_KEY = 'patent_auth_token'
const TASK_AUTH_USER_ID_KEY = 'patent_auth_user_id'
const TASK_AUTH_MODE_KEY = 'patent_auth_mode'

interface StandaloneGuardOptions {
  mode?: string
  defaultScene?: string
}

const toAuthingUser = (userInfo: unknown): AuthingUser => {
  const info = userInfo as any
  return {
    sub: info.sub || info.id,
    name: info.name,
    nickname: info.nickname,
    email: info.email,
    phone: info.phone,
    picture: info.picture || info.photo,
    email_verified: info.email_verified ?? info.emailVerified,
    phone_verified: info.phone_verified ?? info.phoneVerified,
    updated_at: info.updated_at ?? info.updatedAt,
    token: info.token,
    hasPassword: !!info.password
  } as unknown as AuthingUser
}

const getGuardConstructor = (): GuardConstructor | null => {
  if (!process.client) return null
  const guardFactory = (window as Window & { GuardFactory?: { Guard?: GuardConstructor } }).GuardFactory
  return guardFactory && typeof guardFactory.Guard === 'function' ? guardFactory.Guard : null
}

const createStandaloneGuard = (options: StandaloneGuardOptions = {}): GuardClient | null => {
  if (!process.client) return null

  try {
    const config = useRuntimeConfig()
    const appId = String(config.public.authingAppId || '').trim()
    if (!appId) return null

    const host = String(config.public.authingDomain || '').trim()
    const redirectUri = String(config.public.authingRedirectUri || '').trim()
    const Guard = getGuardConstructor()
    if (!Guard) return null

    return new Guard({
      appId,
      ...(host ? { host } : {}),
      ...(redirectUri ? { redirectUri } : {}),
      ...(options.mode ? { mode: options.mode } : {}),
      ...(options.defaultScene ? { defaultScene: options.defaultScene } : {}),
    })
  } catch (_error) {
    return null
  }
}

const getGuardClient = (): GuardClient | null => {
  if (!process.client) return null

  try {
    const nuxtApp = useNuxtApp()
    const guardFromApp = nuxtApp.$guard
    if (guardFromApp) return guardFromApp

    const legacyGuardFromApp = (nuxtApp.vueApp.config.globalProperties as any)?.$guard as GuardClient | undefined
    if (legacyGuardFromApp) return legacyGuardFromApp
  } catch (_error) {
    // ignore and fallback to standalone constructor
  }

  return createStandaloneGuard()
}

export const useAuthStore = defineStore('auth', {
  state: (): AuthState => ({
    user: null,
    isLoggedIn: false,
    loading: false,
    initialized: false,
  }),

  getters: {
    idToken: (state): string => state.user?.token || '',
  },

  actions: {
    _clearBackendSessionCache() {
      if (!process.client) return
      localStorage.removeItem(TASK_AUTH_TOKEN_KEY)
      localStorage.removeItem(TASK_AUTH_USER_ID_KEY)
      localStorage.removeItem(TASK_AUTH_MODE_KEY)
    },

    async checkAuth() {
      if (!process.client) return
      const config = useRuntimeConfig()
      const hasAuthingEnabled = String(config.public.authingAppId || '').trim().length > 0
      if (!hasAuthingEnabled) {
        this.user = null
        this.isLoggedIn = false
        this.initialized = true
        return
      }
      try {
        const guard = getGuardClient()
        if (!guard || typeof guard.trackSession !== 'function') {
          this.user = null
          this.isLoggedIn = false
          return
        }
        const userInfo = await guard.trackSession()
        if (!userInfo) {
          this.user = null
          this.isLoggedIn = false
          return
        }
        this.user = toAuthingUser(userInfo)
        this.isLoggedIn = true
      } catch (error) {
        this.user = null
        this.isLoggedIn = false
        console.error('Authing checkAuth failed:', error)
      } finally {
        this.initialized = true
      }
    },

    async ensureInitialized() {
      if (this.initialized) return
      await this.checkAuth()
    },

    async login() {
      if (!process.client) return
      const config = useRuntimeConfig()
      if (!String(config.public.authingAppId || '').trim()) return
      this.loading = true
      try {
        const guard = getGuardClient()
        if (!guard || typeof guard.startWithRedirect !== 'function') {
          throw new Error('Authing Guard 未初始化。请刷新页面后重试。')
        }
        await guard.startWithRedirect()
      } catch (error) {
        console.error('Authing login failed:', error)
      } finally {
        this.loading = false
      }
    },

    async logout() {
      if (!process.client) return
      const config = useRuntimeConfig()
      if (!String(config.public.authingAppId || '').trim()) {
        this.user = null
        this.isLoggedIn = false
        this.initialized = true
        this._clearBackendSessionCache()
        return
      }
      try {
        const guard = getGuardClient()
        if (guard && typeof guard.logout === 'function') {
          await guard.logout()
        }
      } catch (error) {
        console.error('Authing logout failed:', error)
      } finally {
        this.user = null
        this.isLoggedIn = false
        this.initialized = true
        this._clearBackendSessionCache()
      }
    },

    async handleCallback() {
      if (!process.client) return
      const config = useRuntimeConfig()
      if (!String(config.public.authingAppId || '').trim()) return
      this.loading = true
      try {
        const guard = getGuardClient()
        if (!guard || typeof guard.handleRedirectCallback !== 'function') {
          throw new Error('Authing Guard 未初始化，无法处理回调。')
        }
        await guard.handleRedirectCallback()
      } catch (error) {
        console.error('Authing callback failed:', error)
      } finally {
        this.loading = false
      }
      await this.checkAuth()
      this._clearBackendSessionCache()
    },

    async openPasswordReset() {
      if (!process.client) return
      const config = useRuntimeConfig()
      if (!String(config.public.authingAppId || '').trim()) return

      try {
        // 判断当前用户是否已经有密码
        const hasPassword = (this.user as any)?.hasPassword ?? false

        const targetScene = hasPassword ? 'resetPassword' : 'firstLoginPassword'

        const guard = createStandaloneGuard({
          mode: 'modal',
          defaultScene: targetScene
        })
        if (!guard || typeof guard.render !== 'function') {
          throw new Error('Authing Guard 未初始化，无法打开密码重置页面。')
        }
        await guard.render()
      } catch (error) {
        console.error('Authing openPasswordReset failed:', error)
        try {
          const fallbackGuard = getGuardClient()
          if (fallbackGuard && typeof fallbackGuard.startWithRedirect === 'function') {
            await fallbackGuard.startWithRedirect()
          }
        } catch (fallbackError) {
          console.error('Authing openPasswordReset fallback failed:', fallbackError)
        }
      }
    },
  },
})
