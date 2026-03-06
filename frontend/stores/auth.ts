import { defineStore } from 'pinia'
import { useGuard } from '@authing/guard-vue3'
import type { User } from '@authing/guard-vue3'
import type { AuthState, AuthingUser } from '~/types/auth'

const TASK_AUTH_TOKEN_KEY = 'patent_auth_token'
const TASK_AUTH_USER_ID_KEY = 'patent_auth_user_id'
const TASK_AUTH_MODE_KEY = 'patent_auth_mode'

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
        const guard = useGuard()
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
        const guard = useGuard()
        guard.startWithRedirect()
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
        const guard = useGuard()
        await guard.logout()
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
      const guard = useGuard()
      this.loading = true
      try {
        await guard.handleRedirectCallback()
      } finally {
        this.loading = false
      }
      await this.checkAuth()
      this._clearBackendSessionCache()
    },
  },
})
