import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { PRIMARY_DESTINATIONS } from '../src/app/AppShell'
import { resolveSingleHome } from '../src/api/adapters'
import { Metric } from '../src/components/data-display/Surface'

describe('Single Home production architecture', () => {
  it('exposes exactly four primary destinations', () => {
    expect(PRIMARY_DESTINATIONS.map(({ label, path }) => [label, path])).toEqual([
      ['Home', '/home'],
      ['History', '/history'],
      ['Billing', '/billing'],
      ['Settings', '/settings'],
    ])
  })

  it('requires exactly one active home and ignores retained removed homes', () => {
    expect(resolveSingleHome([{ id: 'one', name: 'Home', lifecycle_state: 'active' }])).toMatchObject({
      state: 'ready',
      home: { id: 'one', name: 'Home' },
    })
    expect(resolveSingleHome([
      { id: 'one', name: 'Home', lifecycle_state: 'active' },
      { id: 'old', name: 'Old home', lifecycle_state: 'removed' },
    ])).toMatchObject({ state: 'ready' })
    expect(resolveSingleHome([
      { id: 'one', name: 'Home', lifecycle_state: 'active' },
      { id: 'two', name: 'Cabin', lifecycle_state: 'active' },
    ])).toMatchObject({ state: 'multiple' })
  })

  it('renders canonical metric identities without duplicate values', () => {
    render(<div><Metric label="Energy" value="4 kWh" identity="home.energy_today" /><Metric label="Cost" value="$1.20" identity="home.cost_today" /></div>)
    const identities = screen.getAllByRole('article').map((node) => node.dataset.metricIdentity)
    expect(new Set(identities).size).toBe(identities.length)
  })
})
