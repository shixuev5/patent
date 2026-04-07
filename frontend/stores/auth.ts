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

interface GuardResolution {
  guard: GuardClient | null
}

const UTF8_DECODER = process.client ? new TextDecoder('utf-8', { fatal: false }) : null
let authInitializationInFlight: Promise<void> | null = null

const decodeBase64Utf8 = (base64Text: string): string => {
  const binary = atob(base64Text)
  const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0))
  return UTF8_DECODER ? UTF8_DECODER.decode(bytes) : binary
}

const containsNonLatin1Chars = (value: string): boolean => Array.from(value).some((char) => char.charCodeAt(0) > 0xff)

const containsLatin1Supplement = (value: string): boolean => /[\u0080-\u00ff]/.test(value)

const normalizeUserText = (value: unknown): string | undefined => {
  if (typeof value !== 'string') return undefined
  const text = value.trim()
  if (!text) return undefined
  if (!process.client || !UTF8_DECODER) return text
  if (containsNonLatin1Chars(text) || !containsLatin1Supplement(text)) return text
  try {
    const bytes = Uint8Array.from(text, (char) => char.charCodeAt(0))
    const repaired = UTF8_DECODER.decode(bytes).trim()
    if (!repaired || repaired === text || repaired.includes('\ufffd')) return text
    return repaired
  } catch (_error) {
    return text
  }
}

const selectPreferredUserText = (primary: unknown, fallback: unknown): string | undefined => {
  const preferred = normalizeUserText(primary)
  const backup = normalizeUserText(fallback)
  if (!preferred) return backup
  if (!backup) return preferred
  if (preferred.includes('\ufffd') && !backup.includes('\ufffd')) return backup
  return preferred
}

const parseJwtPayload = (token: string): Record<string, unknown> | null => {
  try {
    const parts = String(token || '').split('.')
    if (parts.length < 2) return null
    const payload = parts[1]
    const normalized = payload.replace(/-/g, '+').replace(/_/g, '/')
    const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, '=')
    const decoded = decodeBase64Utf8(padded)
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
    name: selectPreferredUserText(info.name, payload?.name),
    nickname: selectPreferredUserText(info.nickname, payload?.nickname),
    email: selectPreferredUserText(info.email, payload?.email),
    phone: selectPreferredUserText(info.phone, payload?.phone_number),
    picture: selectPreferredUserText(info.picture || info.photo, payload?.picture),
    email_verified: info.email_verified ?? info.emailVerified,
    phone_verified: info.phone_verified ?? info.phoneVerified,
    updated_at: info.updated_at ?? info.updatedAt ?? payload?.updated_at,
    token: info.token || storedToken || undefined,
    hasPassword: !!info.password
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
    name: normalizeUserText(payload?.name),
    nickname: normalizeUserText(payload?.nickname),
    email: normalizeUserText(payload?.email),
    phone: normalizeUserText(payload?.phone_number),
    picture: normalizeUserText(payload?.picture),
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
      if (!process.client) return
      const config = useRuntimeConfig()
      if (!String(config.public.authingAppId || '').trim()) return

      try {
        // 判断当前用户是否已经有密码
        const hasPassword = (this.user as any)?.hasPassword ?? false

        const targetScene = hasPassword ? 'forgetPassword' : 'firstLoginPassword'

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
          const { guard: fallbackGuard } = await resolveGuardClient()
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
