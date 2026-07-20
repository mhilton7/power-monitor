import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { EmptyState, formatMoney, formatNumber, StatusPill } from '../src/components/UI'

describe('shared user interface', () => {
  it('renders health state with readable text', () => {
    render(<StatusPill status="online_synchronized" />)
    expect(screen.getByText('online synchronized')).toBeInTheDocument()
  })

  it('renders accessible empty state copy', () => {
    render(<EmptyState title="No sensors enrolled" message="Create a short-lived token." />)
    expect(screen.getByText('No sensors enrolled')).toBeVisible()
    expect(screen.getByText('Create a short-lived token.')).toBeVisible()
  })

  it('formats quantities and USD amounts', () => {
    expect(formatNumber('1234.56', 1)).toContain('1,234.6')
    expect(formatMoney('4.50')).toContain('$4.50')
  })
})
