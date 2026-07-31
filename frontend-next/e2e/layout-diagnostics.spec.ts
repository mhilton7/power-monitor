import { expect, test, type Page } from '@playwright/test'
import { createHash } from 'node:crypto'
import { mkdir } from 'node:fs/promises'
import { writeFile } from 'node:fs/promises'
import path from 'node:path'
import { PERMISSION_CODES } from '../src/access/permissions'

const captureDirectory = process.env.CAPTURE_LAYOUT_DIR
const capturePhase = process.env.CAPTURE_LAYOUT_PHASE ?? 'diagnostic'
const fullMatrix = process.env.CAPTURE_FULL_MATRIX === '1'

const home = {
  id: 'home-1',
  name: 'Upland Home',
  code: 'upland-home',
  timezone: 'America/Los_Angeles',
  currency: 'USD',
  locale: 'en-US',
  unit_system: 'imperial',
  allowed_cidrs: [],
  allowed_domains: [],
  allow_public_polling: false,
  lifecycle_state: 'active',
  is_default: true,
  revision: 1,
}

const service = {
  id: 'service-1',
  site_id: home.id,
  nickname: 'Home-Energy',
  utility_name: 'SCE',
  generation_provider: 'sce',
  timezone: home.timezone,
  currency: home.currency,
  billing_cycle_start_day: 1,
  cost_scope: 'energy_only',
  status: 'active',
  revision: 2,
  readiness: { rate: 'missing', cost: 'partial', topology_complete: false },
  rate_context: {
    current_plan: null,
    current_version: null,
    current_period: null,
    current_price_per_kwh: null,
    billing_cycle: {
      starts_at: '2026-07-01T07:00:00Z',
      ends_at: '2026-08-01T07:00:00Z',
    },
  },
}

const plans = [
  ['plan-1', 'Default Bill', 'DOMESTIC1', 'tiered'],
  ['plan-2', 'TOU-D 4 PM to 9 PM', 'TOU-D-4-9PM', 'time_of_use'],
  ['plan-3', 'TOU-D 5 PM to 8 PM', 'TOU-D-5-8PM', 'time_of_use'],
  ['plan-4', 'TOU-D-PRIME', 'TOU-D-PRIME', 'time_of_use'],
].map(([id, name, code, pricingModel], index) => ({
  id,
  name,
  code,
  status: 'active',
  lifecycle_revision: 2,
  latest_version: {
    id: `version-${index + 1}`,
    version: index + 1,
    pricing_model: pricingModel,
  },
  versions: [{
    id: `version-${index + 1}`,
    version: index + 1,
    status: 'published',
    publication_status: 'published',
    assignment_status: 'unassigned',
    display_status: 'published',
    pricing_model: pricingModel,
    lifecycle_revision: 1,
    assignments: [],
  }],
}))

const bill = {
  id: 'bill-1',
  utility_account_id: service.id,
  status: 'reviewed',
  extraction_method: 'text',
  created_at: '2026-07-24T23:37:00Z',
  page_count: 6,
  billing_cycle: {
    total_usage_kwh: '951.000',
    full_bill_total: null,
    starts_at: '2026-06-01T07:00:00Z',
    ends_at: '2026-07-01T07:00:00Z',
  },
  blocking_warnings: [],
}

async function mockLayoutServer(page: Page) {
  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url())
    const pathname = url.pathname
    const responses: Record<string, unknown> = {
      '/api/v1/auth/session': {
        authenticated: true,
        bootstrap_required: false,
        user: {
          id: 'owner-1',
          email: 'owner@example.test',
          display_name: 'Home Owner',
          roles: ['admin'],
          permissions: [...PERMISSION_CODES],
          all_sites: true,
          site_ids: [],
        },
      },
      '/api/v1/sites': [home],
      '/api/v1/devices': [],
      '/api/v1/utility-accounts': [service],
      '/api/v1/electric-services/default/current-rate-assignment': {
        schema_version: 'current-rate-assignment/1.0',
        home_id: home.id,
        electric_service_id: service.id,
        service_revision: service.revision,
        assignment: null,
      },
      '/api/v1/configuration-status': {
        schema_version: 'configuration-status/1.0',
        home_id: home.id,
        electric_service_id: service.id,
        state: 'setup_needed',
        label: 'Setup needed',
        summary: '2 blocking and 0 advisory issues.',
        generated_at: '2026-07-25T12:00:00Z',
        issues: [{
          id: 'rate-assignment.missing',
          category: 'rate_plan',
          state: 'setup_needed',
          title: 'Choose a current rate plan',
          what_is_wrong: 'The electric service has no plan effective now.',
          why_it_matters: 'Current prices and cost estimates cannot be calculated.',
          how_to_fix: 'Choose a published version and use Make current.',
          blocking: true,
          action: {
            id: 'rate_assignment.make_current',
            label: 'Choose current plan',
            target: '/billing?advanced=rates&tab=versions',
          },
        }],
      },
      [`/api/v1/utility-accounts/${service.id}/tier-status`]: {
        available: false,
        cycle: {
          starts_at: '2026-07-01T07:00:00Z',
          ends_at: '2026-08-01T07:00:00Z',
        },
        projection_confidence: null,
        coverage_percent: '0',
        tiers: [],
        warnings: [],
      },
      '/api/v1/fleet/summary': {
        current_load_w: '0',
        energy_today_kwh: '0',
        estimated_cost_today: '0',
        reporting_devices: 0,
        active_alerts: 0,
        recent_peak_w: '0',
        has_live_data: false,
        has_energy_data: false,
        has_cost_data: false,
      },
      '/api/v1/notifications': { items: [], page: 1, page_size: 200, total: 0 },
      '/api/v1/admin/utility-bill-imports': [bill],
      '/api/v1/rates/plans': { plans },
      '/api/v1/rates/assignments/conflicts': {
        conflicts: [],
        requires_explicit_resolution: false,
      },
      '/api/v1/admin/rate-sources': { sources: [] },
      '/api/v1/admin/rate-sources/check-runs': [],
      '/api/v1/history/query': {
        scope: { display_name: 'Home total' },
        summary: {
          energy_kwh: null,
          energy_cost: null,
          blended_rate_per_kwh: null,
          peak_power_w: null,
          coverage_percent: '0',
          contributing_sensor_count: 0,
        },
        combined: [],
        warnings: [],
        rate_versions_used: [],
      },
    }
    if (pathname === '/api/v1/events/stream') return route.fulfill({ status: 204 })
    const response = responses[pathname]
    if (response !== undefined) return route.fulfill({ json: response })
    return route.fulfill({ json: [] })
  })
}

async function setZoom(page: Page, zoom: number) {
  await page.evaluate((value) => {
    document.documentElement.style.zoom = String(value)
  }, zoom)
}

async function expectNoOverlap(page: Page, selector: string) {
  const boxes = await page.locator(selector).evaluateAll((elements) => elements.map((element) => {
    const rect = element.getBoundingClientRect()
    return { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom }
  }))
  for (let left = 0; left < boxes.length; left += 1) {
    for (let right = left + 1; right < boxes.length; right += 1) {
      const first = boxes[left]
      const second = boxes[right]
      if (!first || !second) continue
      const overlapWidth = Math.min(first.right, second.right) - Math.max(first.left, second.left)
      const overlapHeight = Math.min(first.bottom, second.bottom) - Math.max(first.top, second.top)
      expect(overlapWidth > 1 && overlapHeight > 1, `${selector} items ${left} and ${right} overlap`).toBe(false)
    }
  }
}

async function expectContainedControls(page: Page, containerSelector: string) {
  const failures = await page.locator(containerSelector).evaluateAll((containers) => containers.flatMap((container) => {
    const outer = container.getBoundingClientRect()
    return [...container.querySelectorAll<HTMLElement>('button, input, select')].flatMap((control) => {
      if (control.closest('.subnav, .segmented-control, .table-scroll')) return []
      const rect = control.getBoundingClientRect()
      return rect.left < outer.left - 1 || rect.right > outer.right + 1
        ? [{ label: control.getAttribute('aria-label') ?? control.textContent?.trim() ?? control.tagName, rect, outer }]
        : []
    })
  }))
  expect(failures, `${containerSelector} contains horizontally overflowing controls`).toEqual([])
}

async function capture(page: Page, name: string) {
  if (!captureDirectory) return
  await mkdir(captureDirectory, { recursive: true })
  await page.screenshot({
    path: path.join(captureDirectory, `${capturePhase}-${name}.png`),
    fullPage: true,
    animations: 'disabled',
  })
}

async function captureRuntimeEvidence(page: Page) {
  if (!captureDirectory) return
  const evidence = await page.evaluate(async () => {
    const stylesheetLinks = [...document.querySelectorAll<HTMLLinkElement>('link[rel="stylesheet"]')]
    const stylesheets = await Promise.all(
      stylesheetLinks.map(async (link) => {
        const response = await fetch(link.href)
        return {
          href: link.href,
          status: response.status,
          contentType: response.headers.get('content-type'),
          body: await response.text(),
        }
      }),
    )
    const styles = (selector: string) => {
      const element = document.querySelector(selector)
      if (!(element instanceof HTMLElement)) return null
      const computed = getComputedStyle(element)
      return {
        display: computed.display,
        gridTemplateColumns: computed.gridTemplateColumns,
        flexDirection: computed.flexDirection,
        gap: computed.gap,
        maxWidth: computed.maxWidth,
        overflowX: computed.overflowX,
      }
    }
    return {
      location: window.location.href,
      title: document.title,
      document: {
        clientWidth: document.documentElement.clientWidth,
        scrollWidth: document.documentElement.scrollWidth,
      },
      tokens: {
        contentMax: getComputedStyle(document.documentElement).getPropertyValue('--content-max').trim(),
        space4: getComputedStyle(document.documentElement).getPropertyValue('--space-4').trim(),
      },
      computed: {
        pageStack: styles('.page-stack'),
        billingTopMetrics: styles('.billing-top-metrics'),
        billingMainGrid: styles('.billing-main-grid'),
        serviceFacts: styles('.service-facts'),
        subnav: styles('.subnav'),
        metric: styles('.metric'),
      },
      stylesheets,
    }
  })
  await mkdir(captureDirectory, { recursive: true })
  const serializable = {
    ...evidence,
    stylesheets: evidence.stylesheets.map(({ body, ...stylesheet }) => ({
      ...stylesheet,
      bytes: Buffer.byteLength(body),
      sha256: createHash('sha256').update(body).digest('hex'),
      containsBillingGridRule: body.includes('.billing-top-metrics'),
      containsServiceFactsRule: body.includes('.service-facts'),
      containsSubnavRule: body.includes('.subnav'),
    })),
  }
  await writeFile(
    path.join(captureDirectory, `${capturePhase}-runtime-evidence.json`),
    `${JSON.stringify(serializable, null, 2)}\n`,
    'utf8',
  )
}

test.beforeEach(async ({ page }, testInfo) => {
  await mockLayoutServer(page)
  await page.addInitScript((theme) => {
    localStorage.setItem('pm-single-home-onboarding-complete', 'true')
    localStorage.setItem('pm-theme', theme)
  }, testInfo.project.name === 'desktop-light' ? 'light' : 'dark')
})

test('captures required layout reproduction matrix', async ({ page }, testInfo) => {
  test.skip(!captureDirectory || testInfo.project.name !== 'desktop')
  const viewports = fullMatrix
    ? [
        { width: 3440, height: 1440 },
        { width: 2560, height: 1440 },
        { width: 1920, height: 1080 },
        { width: 1440, height: 900 },
        { width: 1024, height: 768 },
        { width: 768, height: 1024 },
        { width: 390, height: 844 },
      ]
    : [{ width: 1920, height: 1080 }]
  for (const viewport of viewports) {
    await page.setViewportSize(viewport)
    await page.goto('/billing?advanced=rates')
    await expect(page.getByRole('heading', { name: 'Billing', exact: true })).toBeVisible()
    if (viewport.width === 1920) await captureRuntimeEvidence(page)
    await capture(page, `billing-expanded-${viewport.width}x${viewport.height}-zoom100`)
  }
  await page.setViewportSize({ width: 1920, height: 1080 })
  for (const zoom of fullMatrix ? [0.8, 1, 1.25, 1.5] : [1]) {
    await page.goto('/history')
    await setZoom(page, zoom)
    await expect(page.getByRole('heading', { name: 'History', exact: true })).toBeVisible()
    await capture(page, `history-1920x1080-zoom${Math.round(zoom * 100)}`)
    await page.goto('/billing?advanced=rates')
    await setZoom(page, zoom)
    await page.getByRole('tab', { name: 'Versions' }).click()
    await capture(page, `billing-versions-1920x1080-zoom${Math.round(zoom * 100)}`)
  }
})

test('compiled stylesheet contains the shared layout contracts', async ({ page }) => {
  await page.goto('/billing?advanced=rates')
  const evidence = await page.evaluate(async () => {
    const link = document.querySelector<HTMLLinkElement>('link[rel="stylesheet"]')
    const css = link
      ? await fetch(link.href).then((response) => response.text())
      : [...document.querySelectorAll('style')].map((style) => style.textContent ?? '').join('\n')
    return {
      link: link?.getAttribute('href') ?? 'development style injection',
      css,
    }
  })
  expect(evidence.css).toContain('.billing-top-metrics')
  expect(evidence.css).toContain('.metadata-list')
  expect(evidence.css).toContain('.history-summary')
  expect(evidence.css).toContain('.advanced-disclosure')
  expect(evidence.css).toContain('.structured-list')
  expect(evidence.css).not.toContain('<div id="root">')
})

test('Billing, History, and Advanced Rates remain overlap-free at the required viewport and zoom matrix', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop')
  test.setTimeout(180_000)
  const viewports = [
    { width: 3440, height: 1440 },
    { width: 2560, height: 1440 },
    { width: 1920, height: 1080 },
    { width: 1440, height: 900 },
    { width: 1024, height: 768 },
    { width: 768, height: 1024 },
    { width: 390, height: 844 },
  ]
  for (const viewport of viewports) {
    for (const zoom of [0.8, 1, 1.25, 1.5]) {
      await page.setViewportSize({
        width: Math.round(viewport.width / zoom),
        height: Math.round(viewport.height / zoom),
      })
      await page.goto('/billing?advanced=rates')
      await expect(page.getByRole('heading', { name: 'Billing', exact: true })).toBeVisible()
      await expectNoOverlap(page, '.billing-top-metrics > .metric')
      await expectNoOverlap(page, '.metadata-list > .metadata-item')
      await expectNoOverlap(page, '.card-actions > .button, .card-actions > .more-menu')
      await expectContainedControls(page, '.billing-page')
      const billingWidth = await page.evaluate(() => ({
        client: document.documentElement.clientWidth,
        scroll: document.documentElement.scrollWidth,
        offenders: [...document.querySelectorAll<HTMLElement>('body *')]
          .filter((element) => {
            const rect = element.getBoundingClientRect()
            return rect.right > document.documentElement.clientWidth + 1 || rect.left < -1
          })
          .slice(0, 8)
          .map((element) => {
            const rect = element.getBoundingClientRect()
            return { tag: element.tagName, className: element.className, rect: { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom, width: rect.width, height: rect.height } }
          }),
      }))
      expect(billingWidth.scroll - billingWidth.client, `Billing overflow at ${viewport.width}x${viewport.height}, ${zoom * 100}%: ${JSON.stringify(billingWidth.offenders)}`).toBeLessThanOrEqual(1)

      await page.getByRole('tab', { name: 'Versions' }).click()
      await expect(page.getByRole('tabpanel')).toBeVisible()
      await expectNoOverlap(page, '.structured-list > li')

      await page.goto('/history')
      await expect(page.getByRole('heading', { name: 'History', exact: true })).toBeVisible()
      await expectNoOverlap(page, '.history-summary > .metric')
      await expectContainedControls(page, '.history-page')
      const historyWidth = await page.evaluate(() => ({
        client: document.documentElement.clientWidth,
        scroll: document.documentElement.scrollWidth,
        offenders: [...document.querySelectorAll<HTMLElement>('body *')]
          .filter((element) => {
            const rect = element.getBoundingClientRect()
            return rect.right > document.documentElement.clientWidth + 1 || rect.left < -1
          })
          .slice(0, 8)
          .map((element) => {
            const rect = element.getBoundingClientRect()
            return { tag: element.tagName, className: element.className, rect: { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom, width: rect.width, height: rect.height } }
          }),
      }))
      expect(historyWidth.scroll - historyWidth.client, `History overflow at ${viewport.width}x${viewport.height}, ${zoom * 100}%: ${JSON.stringify(historyWidth.offenders)}`).toBeLessThanOrEqual(1)
    }
  }
})

test('Advanced Rate Settings tabs expose keyboard and ARIA relationships', async ({ page }) => {
  await page.goto('/billing?advanced=rates')
  const tab = page.getByRole('tab', { name: 'Custom editor' })
  await tab.focus()
  await page.keyboard.press('ArrowRight')
  const sources = page.getByRole('tab', { name: 'Sources' })
  await expect(sources).toBeFocused()
  await expect(sources).toHaveAttribute('aria-selected', 'true')
  const panelId = await sources.getAttribute('aria-controls')
  expect(panelId).toBeTruthy()
  await expect(page.locator(`#${panelId ?? ''}`)).toHaveAttribute('role', 'tabpanel')
  await expect(page.locator(`#${panelId ?? ''}`)).toHaveAttribute('aria-labelledby', await sources.getAttribute('id') ?? '')
  const duplicateIds = await page.evaluate(() => {
    const ids = [...document.querySelectorAll<HTMLElement>('[id]')].map((element) => element.id)
    return ids.filter((id, index) => ids.indexOf(id) !== index)
  })
  expect(duplicateIds).toEqual([])
  await expect(page.locator('main#main-content')).toBeVisible()
  await expect(page.getByRole('navigation', { name: 'Primary' })).toBeVisible()
})

test('visual regression for repaired Billing and History layouts', async ({ page }, testInfo) => {
  test.skip(!['desktop', 'desktop-light', 'mobile'].includes(testInfo.project.name))
  await page.goto('/billing?advanced=rates')
  await expect(page.getByRole('heading', { name: 'Billing', exact: true })).toBeVisible()
  await expect(page).toHaveScreenshot('billing-repaired.png', { fullPage: true, animations: 'disabled' })
  await page.goto('/history')
  await expect(page.getByRole('heading', { name: 'History', exact: true })).toBeVisible()
  await expect(page).toHaveScreenshot('history-repaired.png', { fullPage: true, animations: 'disabled' })
})
