import { defineStore } from 'pinia'
import { Guard, useGuard } from '@authing/guard-vue3'
import type { User } from '@authing/guard-vue3'
import type { GuardOptions } from '@authing/guard-vue3'
import type { AuthState, AuthingUser } from '~/types/auth'

const TASK_AUTH_TOKEN_KEY = 'patent_auth_token'
const TASK_AUTH_USER_ID_KEY = 'patent_auth_user_id'
const TASK_AUTH_MODE_KEY = 'patent_auth_mode'

interface StandaloneGuardOptions {
  mode?: GuardOptions['mode']
  defaultScene?: GuardOptions['defaultScene']
}

const toAuthingUser = (userInfo: User): AuthingUser => ({
  sub: userInfo.sub,
  name: userInfo.name,
  nickname: userInfo.nickname,
  email: userInfo.email,
  phone: userInfo.phone,
  picture: userInfo.picture,
  email_verified: userInfo.email_verified,
  phone_verified: userInfo.phone_verified,
  updated_at: userInfo.updated_at,
  token: (userInfo as any)?.token,
})

const createStandaloneGuard = (options: StandaloneGuardOptions = {}): Guard | null => {
  if (!process.client) return null

  try {
    const config = useRuntimeConfig()
    const appId = String(config.public.authingAppId || '').trim()
    if (!appId) return null

    const host = String(config.public.authingDomain || '').trim()
    const redirectUri = String(config.public.authingRedirectUri || '').trim()
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

const getGuardClient = (): Guard | null => {
  if (!process.client) return null

  try {
    const nuxtApp = useNuxtApp()
    const guardFromApp = (nuxtApp.vueApp.config.globalProperties as any)?.$guard as Guard | undefined
    if (guardFromApp) return guardFromApp
  } catch (_error) {
    // ignore and try useGuard()
  }

  try {
    const guard = useGuard()
    return guard || null
  } catch (_error) {
    return createStandaloneGuard()
  }
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
        const guard = createStandaloneGuard({
          mode: 'modal',
        })
        if (!guard || typeof guard.render !== 'function') {
          throw new Error('Authing Guard 未初始化，无法打开密码重置页面。')
        }
        await guard.render()
        await new Promise((resolve) => setTimeout(resolve, 0))
        if (typeof guard.changeView === 'function') {
          await guard.changeView('resetPassword')
          const currentModule = String(guard.getCurrentView?.().currentModule || '')
          if (!currentModule || currentModule === 'login') {
            await guard.changeView('forgetPassword')
          }
        }
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
