import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { pageFromPath, StatusIndicatorZone } from '../src/components/StatusIndicators'
import type {
  StatusDensity,
  StatusIndicatorDefinition,
  StatusIndicatorValue,
  StatusLayoutItem,
  StatusResolvedLayout,
} from '../src/types'

function definition(key: string, label: string): StatusIndicatorDefinition {
  return {
    key,
    default_label: label,
    description: `${label} status`,
    category: 'data',
    data_source: 'existing server data',
    current_value_schema: { display_value: 'string' },
    severity_capability: ['success', 'warning', 'critical'],
    default_enabled: true,
    default_zone: 'page_summary',
    allowed_zones: ['page_summary'],
    default_order: 10,
    supported_pages: ['overview'],
    global_shell_support: false,
    minimum_display_width: 160,
    preferred_display_width: 220,
    presentations: ['compact', 'standard', 'detailed'],
    icon_supported: true,
    label_supported: true,
    value_supported: true,
    freshness_supported: true,
    role_visibility_supported: true,
    permission_required: 'status_indicators.view',
    configurable: true,
    renderer: 'summary',
    icon: 'activity',
    metric_identity: key,
    canonical_priority: 300,
    allow_duplicate: false,
    suppress_when_empty: true,
    hide_in_zero_data_state: false,
    diagnostics_only: false,
    registry_version: 'status-indicators/1.0',
  }
}

function item(key: string, label: string, density: StatusDensity = 'standard'): StatusLayoutItem {
  return {
    indicator_key: key,
    page: 'overview',
    role: '*',
    breakpoint: 'default',
    visible: true,
    zone: 'page_summary',
    order: 10,
    density,
    show_icon: true,
    show_label: true,
    show_value: true,
    show_freshness: true,
    show_severity: true,
    show_tooltip: true,
    definition: definition(key, label),
  }
}

function layout(items: StatusLayoutItem[]): StatusResolvedLayout {
  return {
    schema_version: 'power-monitor-status-layout/1.0',
    registry_version: 'status-indicators/1.0',
    published_revision: 2,
    page: 'overview',
    roles: ['admin'],
    breakpoint: 'desktop',
    zones: items.length ? [{ key: 'page_summary', items }] : [],
    warnings: [],
    personalization_enabled: false,
  }
}

const values: Record<string, StatusIndicatorValue> = {
  'data.energy_today': {
    status: 'available',
    severity: 'success',
    display_value: '12.5 kWh',
    detail: 'Since local midnight',
    freshness_at: '2026-07-20T12:00:00Z',
  },
  'device.offline_count': {
    status: 'warning',
    severity: 'warning',
    display_value: '1',
    detail: 'One signed heartbeat is stale',
  },
}

describe('status indicator renderer', () => {
  it('omits the semantic zone wrapper when no indicators are visible', () => {
    const { container } = render(<StatusIndicatorZone zone="page_summary" layout={layout([])} values={{}} />)
    expect(container).toBeEmptyDOMElement()
    expect(container.querySelector('[data-status-zone]')).toBeNull()
  })

  it.each([1, 2, 4])('renders %i configured items without spacer elements', (count) => {
    const items = Array.from({ length: count }, (_, index) => item(`indicator.${index}`, `Indicator ${index + 1}`))
    const itemValues = Object.fromEntries(items.map((entry, index) => [entry.indicator_key, {
      status: 'available', severity: 'success', display_value: `${index + 1}`,
    } satisfies StatusIndicatorValue]))
    const { container } = render(<StatusIndicatorZone zone="page_summary" layout={layout(items)} values={itemValues} />)
    const zone = container.querySelector('[data-status-zone="page_summary"]')
    expect(zone?.childElementCount).toBe(count)
    expect(zone?.querySelectorAll('[data-indicator-key]')).toHaveLength(count)
    expect(zone?.querySelectorAll('[data-metric-identity]')).toHaveLength(count)
  })

  it('keeps an accessible name when the visible label is disabled', () => {
    const energy = { ...item('data.energy_today', 'Energy today', 'compact'), show_label: false }
    render(<StatusIndicatorZone zone="page_summary" layout={layout([energy])} values={values} />)
    expect(screen.getByRole('article', { name: 'Energy today: 12.5 kWh' })).toBeVisible()
    expect(screen.queryByText('Energy today')).not.toBeInTheDocument()
  })

  it('supports compact, standard, and detailed density without losing severity text', () => {
    const items = [
      item('data.energy_today', 'Energy today', 'compact'),
      item('device.offline_count', 'Offline devices', 'detailed'),
    ]
    const { container } = render(<StatusIndicatorZone zone="page_summary" layout={layout(items)} values={values} />)
    expect(container.querySelector('.status-density-compact')).toBeInTheDocument()
    expect(container.querySelector('.status-density-detailed')).toHaveTextContent('One signed heartbeat is stale')
    expect(screen.getByText('warning')).toBeVisible()
  })

  it('renders long labels as content rather than truncating the accessible name', () => {
    const label = 'A deliberately long translated indicator label that tests responsive wrapping safely'
    const longItem = item('data.energy_today', label)
    render(<StatusIndicatorZone zone="page_summary" layout={layout([longItem])} values={values} />)
    expect(screen.getByRole('article', { name: `${label}: 12.5 kWh` })).toBeVisible()
    expect(screen.getByText(label)).toBeVisible()
  })
})

describe('status route mapping', () => {
  it('maps shell routes to deterministic layout pages', () => {
    expect(pageFromPath('/')).toBe('overview')
    expect(pageFromPath('/devices/8d53')).toBe('device_detail')
    expect(pageFromPath('/usage')).toBe('usage')
    expect(pageFromPath('/costs')).toBe('costs')
    expect(pageFromPath('/rates/sources')).toBe('rate_sources')
    expect(pageFromPath('/administration/status-indicators')).toBe('administration')
    expect(pageFromPath('/administration/system-health')).toBe('system_health')
  })
})
