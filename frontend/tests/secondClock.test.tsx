import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ElapsedTime } from '../src/components/data-display/ElapsedTime'
import { SecondClockProvider } from '../src/state/SecondClockContext'

describe('shared local second clock', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-07-30T12:00:00Z'))
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('uses one interval for multiple counters and recalculates after missed ticks', () => {
    const interval = vi.spyOn(window, 'setInterval')
    render(
      <SecondClockProvider>
        <div data-testid="home"><ElapsedTime timestamp="2026-07-30T11:59:59Z" /></div>
        <div data-testid="sensor"><ElapsedTime timestamp="2026-07-30T11:59:58Z" /></div>
      </SecondClockProvider>,
    )

    expect(interval).toHaveBeenCalledTimes(1)
    expect(screen.getByTestId('home')).toHaveTextContent('1s ago')
    expect(screen.getByTestId('sensor')).toHaveTextContent('2s ago')
    act(() => {
      vi.setSystemTime(new Date('2026-07-30T12:00:05Z'))
      vi.advanceTimersByTime(1_000)
    })
    expect(screen.getByTestId('home')).toHaveTextContent('7s ago')
    expect(screen.getByTestId('sensor')).toHaveTextContent('8s ago')
  })

  it('resets only when a newer authoritative timestamp is rendered', () => {
    const view = render(
      <SecondClockProvider>
        <ElapsedTime timestamp="2026-07-30T11:59:55Z" />
      </SecondClockProvider>,
    )
    expect(screen.getByText('5s ago')).toBeVisible()
    view.rerender(
      <SecondClockProvider>
        <ElapsedTime timestamp="2026-07-30T12:00:00Z" />
      </SecondClockProvider>,
    )
    expect(screen.getByText('Just now')).toBeVisible()
  })

  it('does not reject a server receipt while the independently fetched server clock catches up', () => {
    const view = render(
      <SecondClockProvider>
        <ElapsedTime
          timestamp="2026-07-30T12:00:10Z"
          serverNow="2026-07-30T12:00:00Z"
          serverReceipt
        />
      </SecondClockProvider>,
    )

    expect(screen.getByText('Just now')).toBeVisible()
    expect(screen.queryByText('Invalid timestamp')).not.toBeInTheDocument()

    view.rerender(
      <SecondClockProvider>
        <ElapsedTime
          timestamp="2026-07-30T12:00:10Z"
          serverNow="2026-07-30T12:00:12Z"
          serverReceipt
        />
      </SecondClockProvider>,
    )
    expect(screen.getByText('2s ago')).toBeVisible()
  })

  it('continues rejecting future non-receipt timestamps', () => {
    render(
      <SecondClockProvider>
        <ElapsedTime
          timestamp="2026-07-30T12:00:10Z"
          serverNow="2026-07-30T12:00:00Z"
        />
      </SecondClockProvider>,
    )

    expect(screen.getByText('Invalid timestamp')).toBeVisible()
  })
})
