import { describe, expect, it, vi } from 'vitest'
import { api, apiDownload, jsonBody } from '../src/api'

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

  it('downloads an audited CSV with the same CSRF controls', async () => {
    document.cookie = 'pm_csrf=history-proof'
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('power-monitor-history-export/1.0\n', {
        status: 200,
        headers: { 'Content-Type': 'text/csv' },
      }),
    )
    const blob = await apiDownload('/api/v1/history/export', jsonBody({ scope: { type: 'site', site_id: 'site-1' } }))
    const contents = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader()
      reader.addEventListener('load', () => {
        resolve(typeof reader.result === 'string' ? reader.result : '')
      })
      reader.addEventListener('error', () => {
        reject(reader.error ?? new Error('CSV blob could not be read'))
      })
      reader.readAsText(blob)
    })
    expect(contents).toContain('power-monitor-history-export/1.0')
    const [, request] = fetchMock.mock.calls[0] ?? []
    expect(new Headers(request?.headers).get('X-CSRF-Token')).toBe('history-proof')
  })
})
