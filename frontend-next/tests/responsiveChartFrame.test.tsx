import { act, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ResponsiveChartFrame } from '../src/components/charts/ResponsiveChartFrame'

type FrameCallback = FrameRequestCallback

class MockResizeObserver {
  static instances: MockResizeObserver[] = []

  readonly observe = vi.fn()
  readonly unobserve = vi.fn()
  readonly disconnect = vi.fn()

  constructor(private readonly callback: ResizeObserverCallback) {
    MockResizeObserver.instances.push(this)
  }

  trigger(width: number, height: number) {
    const entry = { contentRect: { width, height } } as unknown as ResizeObserverEntry
    this.callback([entry], this)
  }
}

describe('ResponsiveChartFrame', () => {
  let nextFrame = 1
  let frames: Map<number, FrameCallback>

  beforeEach(() => {
    MockResizeObserver.instances = []
    frames = new Map()
    nextFrame = 1
    vi.stubGlobal('ResizeObserver', MockResizeObserver)
    vi.stubGlobal('requestAnimationFrame', vi.fn((callback: FrameCallback) => {
      const id = nextFrame++
      frames.set(id, callback)
      return id
    }))
    vi.stubGlobal('cancelAnimationFrame', vi.fn((id: number) => {
      frames.delete(id)
    }))
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  const flushFrames = () => {
    const scheduled = [...frames.values()]
    frames.clear()
    act(() => {
      scheduled.forEach((callback) => { callback(0) })
    })
  }

  it('resizes the existing chart across shrink and expansion without remounting', () => {
    const chart = { resize: vi.fn() }
    const chartRef = { current: chart }
    const { getByTestId } = render(
      <ResponsiveChartFrame chartRef={chartRef} variant="history">
        <canvas />
      </ResponsiveChartFrame>,
    )

    const observer = MockResizeObserver.instances[0]
    expect(observer).toBeDefined()
    const sizes: Array<readonly [number, number]> = [[1200, 480], [900, 430], [600, 360], [390, 288], [1200, 480]]
    for (const [width, height] of sizes) {
      act(() => { observer?.trigger(width, height) })
      flushFrames()
    }

    expect(chart.resize.mock.calls).toEqual([
      [1200, 480],
      [900, 430],
      [600, 360],
      [390, 288],
      [1200, 480],
    ])
    expect(getByTestId('responsive-chart-frame')).toHaveClass('history-chart')
    expect(MockResizeObserver.instances).toHaveLength(1)
  })

  it('ignores zero and subpixel-only changes, then accepts a later nonzero size', () => {
    const chart = { resize: vi.fn() }
    const { unmount } = render(
      <ResponsiveChartFrame chartRef={{ current: chart }} variant="home">
        <canvas />
      </ResponsiveChartFrame>,
    )
    const observer = MockResizeObserver.instances[0]

    act(() => {
      observer?.trigger(0, 0)
      observer?.trigger(720, 320)
    })
    flushFrames()
    act(() => { observer?.trigger(720.4, 320.4) })
    flushFrames()

    expect(chart.resize).toHaveBeenCalledTimes(1)
    expect(chart.resize).toHaveBeenCalledWith(720, 320)
    unmount()
    expect(observer?.disconnect).toHaveBeenCalledOnce()
  })

  it('cancels pending work and disconnects the observer on unmount', () => {
    const chart = { resize: vi.fn() }
    const { unmount } = render(
      <ResponsiveChartFrame chartRef={{ current: chart }}>
        <canvas />
      </ResponsiveChartFrame>,
    )
    const observer = MockResizeObserver.instances[0]
    act(() => { observer?.trigger(800, 400) })
    expect(frames.size).toBe(1)

    unmount()

    expect(frames.size).toBe(0)
    expect(observer?.disconnect).toHaveBeenCalledOnce()
    expect(chart.resize).not.toHaveBeenCalled()
  })

  it('uses a bounded window resize fallback when ResizeObserver is unavailable', () => {
    vi.stubGlobal('ResizeObserver', undefined)
    const chart = { resize: vi.fn() }
    const { getByTestId, unmount } = render(
      <ResponsiveChartFrame chartRef={{ current: chart }}>
        <canvas />
      </ResponsiveChartFrame>,
    )
    const frame = getByTestId('responsive-chart-frame')
    vi.spyOn(frame, 'getBoundingClientRect').mockReturnValue({ width: 640, height: 360 } as DOMRect)

    act(() => { window.dispatchEvent(new Event('resize')) })
    flushFrames()
    expect(chart.resize).toHaveBeenCalledWith(640, 360)
    unmount()
  })
})
