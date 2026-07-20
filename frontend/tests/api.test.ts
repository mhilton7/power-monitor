import { describe, expect, it, vi } from 'vitest'
import { api, jsonBody } from '../src/api'

describe('same-origin API client', () => {
  it('adds JSON and CSRF headers to mutations', async () => {
    document.cookie = 'pm_csrf=proof%20value'
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    await expect(api<{ ok: boolean }>('/api/v1/example', jsonBody({ enabled: true }))).resolves.toEqual({ ok: true })
    const [, request] = fetchMock.mock.calls[0] ?? []
    expect(request?.credentials).toBe('same-origin')
    const headers = new Headers(request?.headers)
    expect(headers.get('Content-Type')).toBe('application/json')
    expect(headers.get('X-CSRF-Token')).toBe('proof value')
  })

  it('preserves RFC 9457-style problem details', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ title: 'Denied', detail: 'Insufficient role', status: 403, code: 'forbidden' }), {
        status: 403,
        headers: { 'Content-Type': 'application/problem+json' },
      }),
    )
    await expect(api('/api/v1/admin')).rejects.toMatchObject({
      problem: { status: 403, code: 'forbidden' },
    })
  })
})
