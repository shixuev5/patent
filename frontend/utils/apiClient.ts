import { getQueryClient } from '~/utils/queryClient'

export class ApiRequestError extends Error {
  status: number
  body: unknown

  constructor(message: string, status: number, body: unknown) {
    super(message)
    this.name = 'ApiRequestError'
    this.status = status
    this.body = body
  }
}

const joinUrl = (baseUrl: string, path: string): string => {
  const normalizedBase = String(baseUrl || '').replace(/\/+$/, '')
  const normalizedPath = String(path || '').replace(/^\/+/, '')
  return `${normalizedBase}/${normalizedPath}`
}

const parseResponseBody = async (response: Response): Promise<unknown> => {
  const contentType = String(response.headers.get('content-type') || '').toLowerCase()
  if (contentType.includes('application/json')) {
    try {
      return await response.json()
    } catch (_error) {
      return null
    }
  }
  try {
    return await response.text()
  } catch (_error) {
    return null
  }
}

const inferErrorMessage = (status: number, body: unknown): string => {
  if (body && typeof body === 'object') {
    const detail = (body as Record<string, unknown>).detail
    if (typeof detail === 'string' && detail.trim()) return detail
    if (detail && typeof detail === 'object') {
      const detailMessage = (detail as Record<string, unknown>).message
      if (typeof detailMessage === 'string' && detailMessage.trim()) return detailMessage
    }
    const message = (body as Record<string, unknown>).message
    if (typeof message === 'string' && message.trim()) return message
  }
  if (typeof body === 'string' && body.trim()) return body
  return `请求失败（HTTP ${status}）`
}

interface RequestRawOptions {
  baseUrl: string
  path: string
  method?: string
  token?: string
  headers?: HeadersInit
  body?: BodyInit | null
  signal?: AbortSignal
}

export const requestRaw = async ({
  baseUrl,
  path,
  method = 'GET',
  token,
  headers,
  body,
  signal,
}: RequestRawOptions): Promise<Response> => {
  const mergedHeaders = new Headers(headers || {})
  if (token) {
    mergedHeaders.set('Authorization', `Bearer ${token}`)
  }

  return fetch(joinUrl(baseUrl, path), {
    method,
    headers: mergedHeaders,
    body: body ?? null,
    signal,
  })
}

interface RequestJsonOptions extends RequestRawOptions {
  throwOnError?: boolean
}

export const requestJson = async <T>({
  throwOnError = true,
  ...options
}: RequestJsonOptions): Promise<T> => {
  const response = await requestRaw(options)
  const body = await parseResponseBody(response)
  if (!response.ok && throwOnError) {
    throw new ApiRequestError(inferErrorMessage(response.status, body), response.status, body)
  }
  return body as T
}

interface CachedGetJsonOptions {
  baseUrl: string
  path: string
  queryKey: readonly unknown[]
  token?: string
  staleTime?: number
  gcTime?: number
  persist?: boolean
}

export const cachedGetJson = async <T>({
  baseUrl,
  path,
  queryKey,
  token,
  staleTime = 30 * 1000,
  gcTime = 24 * 60 * 60 * 1000,
  persist = true,
}: CachedGetJsonOptions): Promise<T> => {
  const queryClient = getQueryClient()
  const queryFn = async () => {
    return requestJson<T>({
      baseUrl,
      path,
      token,
      method: 'GET',
    })
  }

  if (!queryClient) {
    return queryFn()
  }

  return queryClient.fetchQuery<T>({
    queryKey,
    queryFn,
    staleTime,
    gcTime,
    meta: { persist },
  })
}

export const invalidateQueries = async (queryKeyPrefix: readonly unknown[]) => {
  const queryClient = getQueryClient()
  if (!queryClient) return
  await queryClient.invalidateQueries({ queryKey: queryKeyPrefix })
}

export const setCachedQueryData = <T>(queryKey: readonly unknown[], data: T) => {
  const queryClient = getQueryClient()
  if (!queryClient) return
  queryClient.setQueryData(queryKey, data)
}
