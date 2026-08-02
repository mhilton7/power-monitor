import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import {
  CoverageExplanation,
  coverageSummary,
} from '../src/components/data-display/CoverageExplanation'

describe('coverage explanation', () => {
  it('describes partial coverage as stored history rather than sensor uptime', () => {
    render(<CoverageExplanation value="75" combined />)

    expect(screen.getByText('Reading coverage: 75%')).toBeInTheDocument()
    expect(screen.getByText('75% of the expected sensor readings are stored for this period.')).toBeInTheDocument()
    expect(screen.getByText(/not whether a sensor is online right now/i)).toBeInTheDocument()
    expect(screen.getByText(/every required sensor must provide a usable reading/i)).toBeInTheDocument()
  })

  it('gives clear complete and unavailable summaries', () => {
    expect(coverageSummary('100')).toBe('All expected sensor readings are stored for this period.')
    expect(coverageSummary(undefined)).toBe('Stored history is not available yet.')
    expect(coverageSummary('invalid')).toBe('Stored history is not available yet.')
    expect(coverageSummary('101')).toBe('Stored history is not available yet.')
  })
})
