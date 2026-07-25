import type { ApiProblem } from '../types/models'

export class ApiError extends Error {
  constructor(public readonly problem: ApiProblem) {
    super(problem.detail)
    this.name = 'ApiError'
  }
}

function cookie(name: string): string | undefined {
  return document.cookie
    .split('; ')
    .find((part) => part.startsWith(`${name}=`))
    ?.split('=')
    .slice(1)
    .join('=')
}

function problemFallback(status: number, title: string): ApiProblem {
  return {
    title,
    detail: `The server returned ${status}. Try again or review system health.`,
    status,
    code: 'request_failed',
  }
}

export async function request<T>(
  path: string,
  init: RequestInit = {},
  adapt?: (value: unknown) => T,
): Promise<T> {
  const method = (init.method ?? 'GET').toUpperCase()
  const headers = new Headers(init.headers)
  if (init.body && !(init.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    const csrf = cookie('pm_csrf')
    if (csrf) headers.set('X-CSRF-Token', decodeURIComponent(csrf))
  }
  const response = await fetch(path, { ...init, headers, credentials: 'same-origin' })
  if (!response.ok) {
    const problem = (await response.json().catch(() => problemFallback(response.status, 'Request failed'))) as ApiProblem
    throw new ApiError(problem)
  }
  if (response.status === 204) return undefined as T
  const payload: unknown = await response.json()
  return adapt ? adapt(payload) : payload as T
}

export async function download(path: string, init: RequestInit = {}): Promise<Blob> {
  const method = (init.method ?? 'GET').toUpperCase()
  const headers = new Headers(init.headers)
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    const csrf = cookie('pm_csrf')
    if (csrf) headers.set('X-CSRF-Token', decodeURIComponent(csrf))
  }
  const response = await fetch(path, { ...init, headers, credentials: 'same-origin' })
  if (!response.ok) {
    const problem = (await response.json().catch(() => problemFallback(response.status, 'Download failed'))) as ApiProblem
    throw new ApiError(problem)
  }
  return response.blob()
}

export function json(method: 'POST' | 'PUT' | 'PATCH' | 'DELETE', value?: unknown): RequestInit {
  return { method, body: value === undefined ? undefined : JSON.stringify(value) }
}

export function saveBlob(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = name
  anchor.click()
  URL.revokeObjectURL(url)
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.problem.detail
  if (error instanceof Error) return error.message
  return 'Something went wrong. Try again.'
}
