export interface GuardClient {
  trackSession?: () => Promise<unknown>
  startWithRedirect?: () => Promise<unknown>
  logout?: () => Promise<unknown>
  handleRedirectCallback?: () => Promise<unknown>
  render?: () => Promise<unknown>
}

export interface GuardConstructor {
  new (options: Record<string, unknown>): GuardClient
}
