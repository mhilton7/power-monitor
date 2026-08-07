import { expect, test, type Page, type TestInfo } from '@playwright/test'
import { PERMISSION_CODES } from '../src/access/permissions'

const HOME_ID = 'history-performance-home'
const NOW = '2026-08-03T19:00:00Z'
const TOTAL_POINTS = 720
const PAGE_SIZE = 500
const TIMING_NAMES = {
  jsonParse: 'power-monitor.history.json-parse',
  adaptation: 'power-monitor.history.adaptation',
  timestampParse: 'power-monitor.history.timestamp-parse',
  chartPreparation: 'power-monitor.history.chart-preparation',
} as const

interface HistoryRequestEvidence {
  page: number
  bytes: number
}

interface BrowserPerformanceEvidence {
  project: string
  viewport: { width: number; height: number } | null
  pointCount: number
  pageSizes: number[]
  initialPageRequests: number
  initialTotalRequests: number
  sseBurstEvents: number
  sseRefreshPageRequests: number
  sseRefreshTotalRequests: number
  domNodeCount: number
  accessibleTableMountedWhileClosed: boolean
  longTaskCount: number
  maxLongTaskMs: number
  timings: Record<string, { count: number; totalMs: number; maxMs: number }>
}

function interval(index: number) {
  const start = Date.parse('2026-07-04T19:00:00Z') + index * 60 * 60 * 1_000
  const end = start + 60 * 60 * 1_000
  const energy = (0.2 + (index % 12) * 0.0025).toFixed(6)
  const cost = (Number(energy) * 0.30863).toFixed(6)
  return {
    interval_start_utc: new Date(start).toISOString(),
    interval_end_utc: new Date(end).toISOString(),
    average_power_w: String(800 + (index % 60)),
    energy_kwh: energy,
    energy_cost: cost,
    rate_per_kwh: '0.30863',
    tou_period: 'Tier 1',
    coverage_percent: '100',
    contributing_sensor_count: 2,
    included_sensor_count: 2,
    rate_contributions: [{ tier_name: 'Tier 1' }],
  }
}

const allIntervals = Array.from({ length: TOTAL_POINTS }, (_, index) => interval(index))

function historyPage(page: number) {
  const startIndex = (page - 1) * PAGE_SIZE
  const combined = allIntervals.slice(startIndex, startIndex + PAGE_SIZE)
  return {
    scope: { display_name: 'Whole Home', timezone: 'America/Los_Angeles' },
    bucket: '1h',
    summary: {
      start_utc: allIntervals[0]?.interval_start_utc,
      end_utc: allIntervals.at(-1)?.interval_end_utc,
      energy_kwh: '144.000000',
      energy_cost: '44.442720',
      average_power_w: '829.5',
      peak_power_w: '859',
      blended_rate_per_kwh: '0.30863',
      coverage_percent: '100',
      contributing_sensor_count: 2,
    },
    combined,
    individual: [],
    warnings: [],
    rate_versions_used: [{ rate_plan_name: 'DOMESTIC' }],
    total_buckets: TOTAL_POINTS,
    page,
    page_size: PAGE_SIZE,
    next_page: page === 1 ? 2 : null,
    next_continuation_token: page === 1 ? 'deterministic-signed-continuation' : null,
  }
}

async function installPerformanceCollector(page: Page) {
  await page.addInitScript(() => {
    type Listener = EventListenerOrEventListenerObject
    type PerformanceWindow = typeof window & {
      __pmPerformanceActive?: boolean
      __pmCollectHistoryPerformance?: boolean
      __pmLongTasks?: number[]
      __pmBeginHistoryPerformance?: () => void
      __pmEmitHistoryReadingBurst?: (count: number) => void
    }

    const target = window as PerformanceWindow
    target.__pmPerformanceActive = false
    target.__pmCollectHistoryPerformance = true
    target.__pmLongTasks = []
    target.__pmBeginHistoryPerformance = () => {
      performance.clearMeasures()
      performance.clearMarks()
      target.__pmLongTasks = []
      target.__pmPerformanceActive = true
    }

    if (typeof PerformanceObserver !== 'undefined') {
      try {
        const observer = new PerformanceObserver((list) => {
          if (!target.__pmPerformanceActive) return
          for (const entry of list.getEntries()) target.__pmLongTasks?.push(entry.duration)
        })
        observer.observe({ type: 'longtask', buffered: false })
      } catch {
        // Chromium exposes longtask entries. Other engines can still execute
        // the correctness assertions without turning diagnostics into failure.
      }
    }

    class FakeEventSource {
      static readonly CONNECTING = 0
      static readonly OPEN = 1
      static readonly CLOSED = 2
      readonly CONNECTING = 0
      readonly OPEN = 1
      readonly CLOSED = 2
      readonly url: string
      readonly withCredentials = false
      readyState = FakeEventSource.OPEN
      onerror: ((this: EventSource, event: Event) => unknown) | null = null
      onmessage: ((this: EventSource, event: MessageEvent) => unknown) | null = null
      onopen: ((this: EventSource, event: Event) => unknown) | null = null
      private readonly listeners = new Map<string, Set<Listener>>()

      constructor(url: string | URL) {
        this.url = String(url)
        instances.add(this)
      }

      addEventListener(type: string, listener: Listener | null): void {
        if (!listener) return
        const listeners = this.listeners.get(type) ?? new Set<Listener>()
        listeners.add(listener)
        this.listeners.set(type, listeners)
      }

      removeEventListener(type: string, listener: Listener | null): void {
        if (listener) this.listeners.get(type)?.delete(listener)
      }

      dispatchEvent(event: Event): boolean {
        for (const listener of this.listeners.get(event.type) ?? []) {
          if (typeof listener === 'function') listener.call(this, event)
          else listener.handleEvent(event)
        }
        return true
      }

      close(): void {
        this.readyState = FakeEventSource.CLOSED
        instances.delete(this)
      }
    }

    const instances = new Set<FakeEventSource>()
    Object.defineProperty(window, 'EventSource', {
      configurable: true,
      value: FakeEventSource,
    })
    target.__pmEmitHistoryReadingBurst = (count: number) => {
      for (let index = 0; index < count; index += 1) {
        const event = new MessageEvent('reading', {
          data: JSON.stringify({
            site_id: 'history-performance-home',
            event_watermark: new Date(Date.parse('2026-08-03T19:01:00Z') + index).toISOString(),
          }),
        })
        for (const source of instances) source.dispatchEvent(event)
      }
    }
  })
}

async function mockServer(page: Page, requests: HistoryRequestEvidence[]) {
  await page.route('**/api/v1/**', async (route) => {
    const pathname = new URL(route.request().url()).pathname
    if (pathname === '/api/v1/history/query') {
      const requestBody = route.request().postDataJSON() as { page?: number }
      const response = historyPage(requestBody.page ?? 1)
      const body = JSON.stringify(response)
      requests.push({ page: requestBody.page ?? 1, bytes: body.length })
      return route.fulfill({ status: 200, contentType: 'application/json', body })
    }
    const responses: Record<string, unknown> = {
      '/api/v1/auth/session': {
        authenticated: true,
        bootstrap_required: false,
        user: {
          id: 'performance-owner',
          email: 'performance@example.test',
          display_name: 'Performance Owner',
          roles: ['admin'],
          permissions: [...PERMISSION_CODES],
          all_sites: true,
          site_ids: [],
        },
      },
      '/api/v1/sites': [{
        id: HOME_ID,
        name: 'Performance Home',
        timezone: 'America/Los_Angeles',
        currency: 'USD',
        lifecycle_state: 'active',
        is_default: true,
        revision: 1,
      }],
      '/api/v1/appearance': {
        chart_power_color: '#78DFBF',
        chart_energy_color: '#78DFBF',
        chart_cost_color: '#C9A7FF',
        revision: 1,
        updated_at: NOW,
      },
      '/api/v1/devices': [],
      '/api/v1/utility-accounts': [],
      '/api/v1/electric-services/default/current-rate-assignment': {
        schema_version: 'current-rate-assignment/1.0',
        home_id: HOME_ID,
        electric_service_id: null,
        assignment: null,
      },
      '/api/v1/configuration-status': {
        schema_version: 'configuration-status/1.0',
        home_id: HOME_ID,
        state: 'ready',
        label: 'Ready',
        summary: 'Monitoring is configured.',
        generated_at: NOW,
        issues: [],
      },
      '/api/v1/fleet/summary': {
        current_load_w: '829.5',
        energy_today_kwh: '12.4',
        estimated_cost_today: '3.83',
        reporting_devices: 2,
        total_devices: 2,
        online_devices: 2,
        active_alerts: 0,
        recent_peak_w: '859',
        latest_data_at: NOW,
        latest_received_at: NOW,
        server_now: NOW,
        has_live_data: true,
        has_energy_data: true,
        has_cost_data: true,
        current_rate_plan: 'DOMESTIC',
        current_rate_price_per_kwh: '0.30863',
        current_tou_bucket: 'Tier 1',
      },
      '/api/v1/notifications': { items: [], page: 1, page_size: 200, total: 0 },
      '/api/v1/test-mode': {
        enabled: false,
        remaining_seconds: 0,
        sensor_count: 0,
        online_sensors: 0,
        offline_sensors: 0,
        sample_interval_seconds: 5,
        cost_preview_enabled: false,
        current_power_w: '0',
        total_energy_kwh: '0',
        source_type: 'simulated',
        environment: 'test_mode',
        isolation: {
          real_readings: true,
          bills_and_finalized_costs: true,
          exports_and_backups: true,
          alerts: true,
          credentials_and_firmware: true,
        },
      },
    }
    const response = responses[pathname]
    return response === undefined
      ? route.fulfill({ status: 200, json: [] })
      : route.fulfill({ status: 200, json: response })
  })
}

function summarize(values: number[]): { count: number; totalMs: number; maxMs: number } {
  return {
    count: values.length,
    totalMs: Number(values.reduce((total, value) => total + value, 0).toFixed(3)),
    maxMs: Number(Math.max(0, ...values).toFixed(3)),
  }
}

async function collectEvidence(
  page: Page,
  testInfo: TestInfo,
  requests: HistoryRequestEvidence[],
  initialRequestCount: number,
  sseEvents: number,
): Promise<BrowserPerformanceEvidence> {
  const runtime = await page.evaluate((names) => {
    type PerformanceWindow = typeof window & { __pmLongTasks?: number[] }
    const measures = Object.fromEntries(Object.entries(names).map(([key, name]) => [
      key,
      performance.getEntriesByName(name, 'measure').map((entry) => entry.duration),
    ]))
    return {
      measures,
      longTasks: (window as PerformanceWindow).__pmLongTasks ?? [],
      domNodeCount: document.querySelectorAll('*').length,
      accessibleTableMountedWhileClosed: document.querySelector('.chart-table:not([open]) table') !== null,
    }
  }, TIMING_NAMES)
  const initial = requests.slice(0, initialRequestCount)
  const refreshed = requests.slice(initialRequestCount)
  return {
    project: testInfo.project.name,
    viewport: page.viewportSize(),
    pointCount: TOTAL_POINTS,
    pageSizes: initial.map((request) => request.page === 1 ? PAGE_SIZE : TOTAL_POINTS - PAGE_SIZE),
    initialPageRequests: initial.filter((request) => request.page === 1).length,
    initialTotalRequests: initial.length,
    sseBurstEvents: sseEvents,
    sseRefreshPageRequests: refreshed.filter((request) => request.page === 1).length,
    sseRefreshTotalRequests: refreshed.length,
    domNodeCount: runtime.domNodeCount,
    accessibleTableMountedWhileClosed: runtime.accessibleTableMountedWhileClosed,
    longTaskCount: runtime.longTasks.length,
    maxLongTaskMs: Number(Math.max(0, ...runtime.longTasks).toFixed(3)),
    timings: Object.fromEntries(Object.entries(runtime.measures).map(([key, values]) => [key, summarize(values)])),
  }
}

test.beforeEach(async ({ page }, testInfo) => {
  test.skip(!['desktop', 'mobile'].includes(testInfo.project.name), 'The representative desktop/mobile Chromium projects collect browser performance.')
  await installPerformanceCollector(page)
  await page.addInitScript(() => {
    localStorage.setItem('pm-single-home-onboarding-complete', 'true')
    localStorage.setItem('pm-theme', 'dark')
    localStorage.setItem('pm-show-daily-chart', 'false')
  })
})

test('warmed production History renders 720 points within browser budgets and coalesces SSE bursts', async ({ page }, testInfo) => {
  const requests: HistoryRequestEvidence[] = []
  const pageErrors: string[] = []
  page.on('pageerror', (error) => { pageErrors.push(error.message) })
  await mockServer(page, requests)

  await page.goto('/home')
  await expect(page.getByRole('heading', { name: /Good (morning|afternoon|evening)/ })).toBeVisible()
  expect(requests).toHaveLength(0)
  await page.evaluate(() => {
    const target = window as typeof window & { __pmBeginHistoryPerformance?: () => void }
    target.__pmBeginHistoryPerformance?.()
  })

  await page.getByRole('link', { name: 'History', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'History', exact: true })).toBeVisible()
  await expect(page.getByText(`${TOTAL_POINTS} time intervals`, { exact: false })).toBeAttached()
  await expect(page.locator('.chart-canvas canvas')).toBeVisible()
  await expect.poll(() => requests.length).toBe(2)
  expect(requests.map((request) => request.page)).toEqual([1, 2])
  expect(requests[0]?.bytes).toBeGreaterThan(150_000)
  expect(requests[1]?.bytes).toBeGreaterThan(70_000)
  await expect(page.locator('.chart-table')).not.toHaveAttribute('open', '')
  await expect(page.locator('.chart-table table')).toHaveCount(0)

  const initialRequestCount = requests.length
  const burstEvents = 25
  await page.evaluate((count) => {
    const target = window as typeof window & { __pmEmitHistoryReadingBurst?: (eventCount: number) => void }
    target.__pmEmitHistoryReadingBurst?.(count)
  }, burstEvents)
  await expect.poll(() => requests.length, { timeout: 5_000 }).toBe(initialRequestCount + 2)
  await page.waitForTimeout(900)
  expect(requests).toHaveLength(initialRequestCount + 2)
  expect(requests.slice(initialRequestCount).map((request) => request.page)).toEqual([1, 2])

  const evidence = await collectEvidence(page, testInfo, requests, initialRequestCount, burstEvents)
  expect(evidence.initialPageRequests).toBe(1)
  expect(evidence.initialTotalRequests).toBe(2)
  expect(evidence.sseRefreshPageRequests).toBe(1)
  expect(evidence.sseRefreshTotalRequests).toBe(2)
  expect(evidence.accessibleTableMountedWhileClosed).toBe(false)
  expect(evidence.domNodeCount).toBeLessThan(1_000)
  expect(evidence.maxLongTaskMs).toBeLessThan(100)
  expect(evidence.timings.jsonParse?.count).toBeGreaterThanOrEqual(4)
  expect(evidence.timings.adaptation?.count).toBeGreaterThanOrEqual(4)
  // React Query structurally shares an unchanged SSE refresh, so the chart
  // preparation should be present but need not repeat for identical data.
  expect(evidence.timings.timestampParse?.count).toBeGreaterThanOrEqual(1)
  expect(evidence.timings.chartPreparation?.count).toBeGreaterThanOrEqual(1)
  expect(pageErrors).toEqual([])

  const serialized = JSON.stringify(evidence, null, 2)
  console.info(`[history-browser-performance] ${JSON.stringify(evidence)}`)
  await testInfo.attach('history-browser-performance.json', {
    body: Buffer.from(serialized),
    contentType: 'application/json',
  })
})
