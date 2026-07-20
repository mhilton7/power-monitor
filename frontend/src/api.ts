import type { ApiProblem } from './types'

export class ApiError extends Error {
  constructor(public readonly problem: ApiProblem) {
    super(problem.detail)
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

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? 'GET').toUpperCase()
  const headers = new Headers(init.headers)
  if (init.body && !(init.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    const csrf = cookie('pm_csrf')
    if (csrf) headers.set('X-CSRF-Token', decodeURIComponent(csrf))
  }
  const response = await fetch(path, { ...init, headers, credentials: 'same-origin' })
  if (!response.ok) {
    const problem = (await response.json().catch(() => ({
      title: 'Request failed',
      detail: `The server returned ${response.status}.`,
      status: response.status,
      code: 'request_failed',
    }))) as ApiProblem
    throw new ApiError(problem)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export const jsonBody = (value: unknown): RequestInit => ({
  method: 'POST',
  body: JSON.stringify(value),
})

