import { defineStore } from 'pinia'
import type { GuardClient, GuardConstructor, GuardWindow } from '~/types/authing-guard-cdn'
import type { AuthState, AuthingUser } from '~/types/auth'

const TASK_AUTH_TOKEN_KEY = 'patent_auth_token'
const TASK_AUTH_ACCESS_TOKEN_KEY = 'patent_auth_access_token'
const TASK_AUTH_REFRESH_TOKEN_KEY = 'patent_auth_refresh_token'
const TASK_AUTH_ACCESS_EXPIRES_AT_KEY = 'patent_auth_access_expires_at'
const TASK_AUTH_REFRESH_EXPIRES_AT_KEY = 'patent_auth_refresh_expires_at'
const TASK_AUTH_USER_ID_KEY = 'patent_auth_user_id'
const TASK_AUTH_MODE_KEY = 'patent_auth_mode'
const AUTHING_ID_TOKEN_KEY = 'idToken'
const AUTHING_ACCESS_TOKEN_KEY = 'accessToken'
const AUTHING_CODE_CHALLENGE_KEY = 'codeChallenge'

interface StandaloneGuardOptions {
  mode?: string
  defaultScene?: string
}

type PasswordResetScene = 'forgetPassword' | 'firstLoginPassword'

interface GuardResolution {
  guard: GuardClient | null
}

let authInitializationInFlight: Promise<void> | null = null

const parseJwtPayload = (token: string): Record<string, unknown> | null => {
  try {
    const parts = String(token || '').split('.')
    if (parts.length < 2) return null
    const payload = parts[1]
    const normalized = payload.replace(/-/g, '+').replace(/_/g, '/')
    const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, '=')
    const decoded = atob(padded)
    return JSON.parse(decoded) as Record<string, unknown>
  } catch (_error) {
    return null
  }
}

const getStoredIdToken = (): string => {
  if (!process.client) return ''
  return String(localStorage.getItem(AUTHING_ID_TOKEN_KEY) || '').trim()
}

const parseGuardLoginStatus = (value: unknown): boolean | null => {
  if (typeof value === 'boolean') return value
  if (!value || typeof value !== 'object') return null

  const record = value as Record<string, unknown>
  if (typeof record.status === 'boolean') return record.status

  const data = record.data
  if (data && typeof data === 'object' && typeof (data as Record<string, unknown>).status === 'boolean') {
    return (data as Record<string, unknown>).status as boolean
  }

  return null
}

const toAuthingUser = (userInfo: unknown): AuthingUser => {
  const info = userInfo as any
  const storedToken = getStoredIdToken()
  const payload = storedToken ? parseJwtPayload(storedToken) : null
  return {
    sub: String(info.sub || info.id || payload?.sub || '').trim(),
    name: typeof info.name === 'string' ? info.name : (typeof payload?.name === 'string' ? payload.name : undefined),
    nickname: typeof info.nickname === 'string' ? info.nickname : (typeof payload?.nickname === 'string' ? payload.nickname : undefined),
    email: typeof info.email === 'string' ? info.email : (typeof payload?.email === 'string' ? payload.email : undefined),
    phone: typeof info.phone === 'string' ? info.phone : (typeof payload?.phone_number === 'string' ? payload.phone_number : undefined),
    picture: typeof (info.picture || info.photo) === 'string' ? (info.picture || info.photo) : (typeof payload?.picture === 'string' ? payload.picture : undefined),
    email_verified: info.email_verified ?? info.emailVerified,
    phone_verified: info.phone_verified ?? info.phoneVerified,
    updated_at: info.updated_at ?? info.updatedAt ?? payload?.updated_at,
    token: info.token || storedToken || undefined,
  } as unknown as AuthingUser
}

const getFallbackUserFromStoredIdToken = (): AuthingUser | null => {
  if (!process.client) return null
  const token = getStoredIdToken()
  if (!token) return null
  const payload = parseJwtPayload(token)
  const sub = String(payload?.sub || '').trim()
  if (!sub) return null
  return {
    sub,
    name: typeof payload?.name === 'string' ? payload.name : undefined,
    nickname: typeof payload?.nickname === 'string' ? payload.nickname : undefined,
    email: typeof payload?.email === 'string' ? payload.email : undefined,
    phone: typeof payload?.phone_number === 'string' ? payload.phone_number : undefined,
    picture: typeof payload?.picture === 'string' ? payload.picture : undefined,
    email_verified: typeof payload?.email_verified === 'boolean' ? payload.email_verified : undefined,
    updated_at: typeof payload?.updated_at === 'string' ? payload.updated_at : undefined,
    token,
  }
}

const resolveGuardSessionUser = async (guard: GuardClient | null): Promise<AuthingUser | null> => {
  const fallbackUser = getFallbackUserFromStoredIdToken()
  if (!guard) return fallbackUser

  let loginStatus: boolean | null = null
  if (typeof guard.checkLoginStatus === 'function') {
    try {
      loginStatus = parseGuardLoginStatus(await guard.checkLoginStatus())
    } catch (_error) {
      loginStatus = null
    }
  }

  if (typeof guard.trackSession === 'function') {
    try {
      const userInfo = await guard.trackSession()
      if (userInfo) return toAuthingUser(userInfo)
    } catch (_error) {
      // Fallback to the local idToken payload when Guard session fetching is transiently unavailable.
    }
  }

  if (loginStatus === false) return fallbackUser
  return fallbackUser
}

const getGuardConstructor = (): GuardConstructor | null => {
  if (!process.client) return null
  const win = window as GuardWindow
  if (typeof win.Guard === 'function') return win.Guard
  const factoryGuard = win.GuardFactory?.Guard
  return typeof factoryGuard === 'function' ? factoryGuard : null
}

const createStandaloneGuard = (options: StandaloneGuardOptions = {}): GuardClient | null => {
  if (!process.client) return null

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
}

const resolveGuardClient = async (options: StandaloneGuardOptions = {}): Promise<GuardResolution> => {
  if (!process.client) return { guard: null }

  try {
    const nuxtApp = useNuxtApp()
    const guardFromApp = nuxtApp.$guard
    if (guardFromApp) return { guard: guardFromApp }

    const legacyGuardFromApp = (nuxtApp.vueApp.config.globalProperties as any)?.$guard as GuardClient | undefined
    if (legacyGuardFromApp) return { guard: legacyGuardFromApp }
  } catch (_error) {
    // ignore and fallback to standalone constructor
  }

  try {
    return { guard: createStandaloneGuard(options) }
  } catch (error) {
    console.error('Authing Guard create instance failed:', error)
    return { guard: null }
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
    idToken: (state): string => state.user?.token || getStoredIdToken(),
  },

  actions: {
    async _openPasswordResetScene(scene: PasswordResetScene) {
      if (!process.client) return
      const config = useRuntimeConfig()
      if (!String(config.public.authingAppId || '').trim()) return

      try {
        const guard = createStandaloneGuard({
          mode: 'modal',
          defaultScene: scene,
        })
        if (!guard || typeof guard.render !== 'function') {
          throw new Error('Authing Guard 未初始化，无法打开密码重置页面。')
        }
        await guard.render()
      } catch (error) {
        console.error('Authing openPasswordReset failed:', error)
        try {
          const { guard: fallbackGuard } = await resolveGuardClient()
          if (fallbackGuard && typeof fallbackGuard.startWithRedirect === 'function') {
            await fallbackGuard.startWithRedirect()
          }
        } catch (fallbackError) {
          console.error('Authing openPasswordReset fallback failed:', fallbackError)
        }
      }
    },

    _clearBackendSessionCache() {
      if (!process.client) return
      localStorage.removeItem(TASK_AUTH_TOKEN_KEY)
      localStorage.removeItem(TASK_AUTH_ACCESS_TOKEN_KEY)
      localStorage.removeItem(TASK_AUTH_REFRESH_TOKEN_KEY)
      localStorage.removeItem(TASK_AUTH_ACCESS_EXPIRES_AT_KEY)
      localStorage.removeItem(TASK_AUTH_REFRESH_EXPIRES_AT_KEY)
      localStorage.removeItem(TASK_AUTH_USER_ID_KEY)
      localStorage.removeItem(TASK_AUTH_MODE_KEY)
    },

    _clearAuthingSessionCache() {
      if (!process.client) return
      localStorage.removeItem(AUTHING_ID_TOKEN_KEY)
      localStorage.removeItem(AUTHING_ACCESS_TOKEN_KEY)
      localStorage.removeItem(AUTHING_CODE_CHALLENGE_KEY)
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
        const { guard } = await resolveGuardClient()
        const user = await resolveGuardSessionUser(guard)
        this.user = user
        this.isLoggedIn = !!user
      } catch (error) {
        const fallbackUser = getFallbackUserFromStoredIdToken()
        this.user = fallbackUser
        this.isLoggedIn = !!fallbackUser
        console.error('Authing checkAuth failed:', error)
      } finally {
        this.initialized = true
      }
    },

    async ensureInitialized() {
      if (this.initialized) return
      if (authInitializationInFlight) {
        await authInitializationInFlight
        return
      }
      authInitializationInFlight = this.checkAuth().finally(() => {
        authInitializationInFlight = null
      })
      await authInitializationInFlight
    },

    async login() {
      if (!process.client) return
      const config = useRuntimeConfig()
      if (!String(config.public.authingAppId || '').trim()) return
      this.loading = true
      try {
        const { guard } = await resolveGuardClient()
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
        const { guard } = await resolveGuardClient()
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
        this._clearAuthingSessionCache()
      }
    },

    async handleCallback(): Promise<boolean> {
      if (!process.client) return false
      const config = useRuntimeConfig()
      if (!String(config.public.authingAppId || '').trim()) return false
      this.loading = true
      try {
        const { guard } = await resolveGuardClient()
        if (!guard || typeof guard.handleRedirectCallback !== 'function') {
          throw new Error('Authing Guard 未初始化，无法处理回调。')
        }
        await guard.handleRedirectCallback()
        const user = await resolveGuardSessionUser(guard)
        this.user = user
        this.isLoggedIn = !!user
      } catch (error) {
        const fallbackUser = getFallbackUserFromStoredIdToken()
        this.user = fallbackUser
        this.isLoggedIn = !!fallbackUser
        console.error('Authing callback failed:', error)
        return !!fallbackUser
      } finally {
        this.loading = false
      }
      this.initialized = true
      await this.checkAuth()
      this._clearBackendSessionCache()
      return this.isLoggedIn
    },

    async openPasswordReset() {
      // 账户页入口固定走普通重置密码场景，不占用首登强制改密的交互流。
      await this._openPasswordResetScene('forgetPassword')
    },

    async openFirstLoginPasswordReset() {
      // 仅在 Authing 明确要求首登强制改密、且当前交互流仍有效时调用。
      await this._openPasswordResetScene('firstLoginPassword')
    },
  },
})
