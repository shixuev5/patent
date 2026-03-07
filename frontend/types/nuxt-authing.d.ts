import type { GuardClient } from './authing-guard-cdn'

declare module '#app' {
  interface NuxtApp {
    $guard?: GuardClient
  }
}

declare module 'vue' {
  interface ComponentCustomProperties {
    $guard?: GuardClient
  }
}

export {}
