import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { persistQueryClient } from '@tanstack/query-persist-client-core'
import { createSyncStoragePersister } from '@tanstack/query-sync-storage-persister'
import { setQueryClient } from '~/utils/queryClient'

const QUERY_PERSIST_KEY = 'patent::vue-query-cache::v1'

export default defineNuxtPlugin((nuxtApp) => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30 * 1000,
        gcTime: 24 * 60 * 60 * 1000,
        retry: 1,
        refetchOnWindowFocus: false,
      },
      mutations: {
        retry: 0,
      },
    },
  })

  nuxtApp.vueApp.use(VueQueryPlugin, { queryClient })
  setQueryClient(queryClient)

  if (process.client) {
    const persister = createSyncStoragePersister({
      storage: window.localStorage,
      key: QUERY_PERSIST_KEY,
      throttleTime: 1000,
      serialize: JSON.stringify,
      deserialize: JSON.parse,
    })
    persistQueryClient({
      queryClient,
      persister,
      maxAge: 3 * 24 * 60 * 60 * 1000,
      buster: 'v1',
      dehydrateOptions: {
        shouldDehydrateQuery: (query) => query.meta?.persist !== false,
      },
    })
  }
})
