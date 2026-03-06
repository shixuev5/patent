export interface AuthingUser {
  sub: string
  name?: string
  nickname?: string
  email?: string
  phone?: string
  picture?: string
  email_verified?: boolean
  phone_verified?: boolean
  updated_at?: string
  token?: string
}

export interface AuthState {
  user: AuthingUser | null
  isLoggedIn: boolean
  loading: boolean
  initialized: boolean
}
