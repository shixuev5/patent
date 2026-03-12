import type { QueryClient } from '@tanstack/vue-query'

let _queryClient: QueryClient | null = null

export const setQueryClient = (client: QueryClient) => {
  _queryClient = client
}

export const getQueryClient = (): QueryClient | null => _queryClient
