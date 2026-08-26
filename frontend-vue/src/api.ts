export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers)
  if (options.body && !(options.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  const response = await fetch(path, { ...options, headers, credentials: 'include' })
  if (!response.ok) {
    let detail = `请求失败（${response.status}）`
    try {
      const body = await response.json()
      detail = body.detail || detail
    } catch {
      // Keep the status-based fallback.
    }
    throw new ApiError(response.status, detail)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}
