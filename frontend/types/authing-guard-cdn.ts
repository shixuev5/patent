export interface GuardClient {
  trackSession?: () => Promise<unknown>
  checkLoginStatus?: () => Promise<unknown>
  startWithRedirect?: () => Promise<unknown>
  logout?: () => Promise<unknown>
  handleRedirectCallback?: () => Promise<unknown>
  render?: () => Promise<unknown>
}

export interface GuardConstructor {
  new (options: Record<string, unknown>): GuardClient
}

export interface GuardWindow extends Window {
  Guard?: GuardConstructor
  GuardFactory?: {
    Guard?: GuardConstructor
  }
}
