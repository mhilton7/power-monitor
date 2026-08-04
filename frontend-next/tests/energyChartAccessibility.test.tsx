import { fireEvent, render, screen } from '@testing-library/react'
import { forwardRef, type ReactNode } from 'react'
import { beforeAll, describe, expect, it, vi } from 'vitest'
import type { HistoryPoint } from '../src/types/models'

vi.mock('react-chartjs-2', () => ({
  Line: forwardRef<HTMLCanvasElement>((_properties, reference) => (
    <canvas aria-label="Energy chart" ref={reference} />
  )),
}))

vi.mock('../src/components/charts/ResponsiveChartFrame', () => ({
  ResponsiveChartFrame: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}))

vi.mock('../src/state/AppearanceContext', () => ({
  useAppearance: () => ({
    chartColors: { power: '#78DFBF', energy: '#78DFBF', cost: '#C9A7FF' },
  }),
}))

import { EnergyChart } from '../src/components/charts/EnergyChart'

beforeAll(() => {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: () => ({ matches: true }),
  })
})

describe('EnergyChart accessible table', () => {
  it('mounts a bounded page of rows only while the native disclosure is open', () => {
    const points: HistoryPoint[] = Array.from({ length: 520 }, (_, index) => ({
      start: new Date(Date.UTC(2026, 6, 1, 0, index)).toISOString(),
      end: new Date(Date.UTC(2026, 6, 1, 0, index + 1)).toISOString(),
      label: String(index),
      coveragePercent: '100',
      energyKwh: '0.001',
      missing: false,
    }))
    const view = render(
      <EnergyChart
        points={points}
        mode="energy"
        currency="USD"
        title="Energy history"
      />,
    )
    const disclosure = view.container.querySelector('details')
    expect(disclosure).not.toBeNull()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()

    if (!disclosure) return
    disclosure.open = true
    fireEvent(disclosure, new Event('toggle'))
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.getAllByRole('row')).toHaveLength(101)
    fireEvent.click(screen.getByRole('button', { name: 'Show next 100 intervals' }))
    expect(screen.getAllByRole('row')).toHaveLength(201)

    disclosure.open = false
    fireEvent(disclosure, new Event('toggle'))
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })
})
