import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { AppearanceProvider, useAppearance } from '../src/state/AppearanceContext'

function Harness() {
  const appearance = useAppearance()
  return <>
    <output aria-label="power color">{appearance.chartColors.power}</output>
    <output aria-label="energy color">{appearance.chartColors.energy}</output>
    <output aria-label="cost color">{appearance.chartColors.cost}</output>
    <button type="button" onClick={() => { appearance.setChartColor('power', '#123456') }}>Set power</button>
    <button type="button" onClick={() => { appearance.setChartColor('power', 'invalid') }}>Set invalid</button>
    <button type="button" onClick={appearance.resetChartColors}>Reset</button>
  </>
}

describe('browser-local chart colors', () => {
  beforeEach(() => { window.localStorage.clear() })

  it('validates stored colors, persists updates, rejects invalid input, and resets defaults', async () => {
    window.localStorage.setItem('pm-chart-power-color', 'not-a-color')
    window.localStorage.setItem('pm-chart-cost-color', '#112233')
    const user = userEvent.setup()
    render(<AppearanceProvider><Harness /></AppearanceProvider>)

    expect(screen.getByLabelText('power color')).toHaveTextContent('#78DFBF')
    expect(screen.getByLabelText('cost color')).toHaveTextContent('#112233')
    await user.click(screen.getByRole('button', { name: 'Set power' }))
    expect(screen.getByLabelText('power color')).toHaveTextContent('#123456')
    await waitFor(() => { expect(window.localStorage.getItem('pm-chart-power-color')).toBe('#123456') })

    await user.click(screen.getByRole('button', { name: 'Set invalid' }))
    expect(screen.getByLabelText('power color')).toHaveTextContent('#123456')
    await user.click(screen.getByRole('button', { name: 'Reset' }))
    expect(screen.getByLabelText('power color')).toHaveTextContent('#78DFBF')
    expect(screen.getByLabelText('energy color')).toHaveTextContent('#78DFBF')
    expect(screen.getByLabelText('cost color')).toHaveTextContent('#C9A7FF')
  })
})
