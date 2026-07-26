import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { BrowserRouter } from '../src/app/router'
import { ConfigurationStatusChip } from '../src/features/configuration/ConfigurationStatusSurface'
import type { ConfigurationStatus } from '../src/types/models'

const status: ConfigurationStatus = {
  homeId: 'home-1',
  electricServiceId: 'service-1',
  state: 'setup_needed',
  label: 'Setup needed',
  summary: '1 blocking and 0 advisory issues.',
  generatedAt: '2026-07-25T12:00:00Z',
  issues: [{
    id: 'rate-assignment.missing',
    category: 'rate_plan',
    state: 'setup_needed',
    title: 'Choose a current rate plan',
    whatIsWrong: 'The electric service has no plan effective now.',
    whyItMatters: 'Current energy prices and cost estimates are unavailable.',
    howToFix: 'Choose a published version and use Make current.',
    blocking: true,
    action: {
      id: 'rate_assignment.make_current',
      label: 'Choose current plan',
      target: '/billing?advanced=rates&tab=versions',
    },
  }],
}

describe('configuration status surface', () => {
  it('opens from the status chip, explains the issue, and routes to the exact fix', async () => {
    const user = userEvent.setup()
    render(<BrowserRouter><ConfigurationStatusChip status={status} /></BrowserRouter>)

    const trigger = screen.getByRole('button', { name: 'Setup needed' })
    expect(trigger).toHaveAttribute('aria-haspopup', 'dialog')
    await user.click(trigger)

    expect(screen.getByRole('dialog', { name: 'Setup needed' })).toBeVisible()
    expect(screen.getByText('What is wrong')).toBeVisible()
    expect(screen.getByText('Why it matters')).toBeVisible()
    expect(screen.getByText('How to fix it')).toBeVisible()
    expect(screen.getByRole('link', { name: /Choose current plan/ })).toHaveAttribute(
      'href',
      '/billing?advanced=rates&tab=versions',
    )

    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(trigger).toHaveFocus()
  })
})
