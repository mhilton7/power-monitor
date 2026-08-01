import { useCallback, useLayoutEffect, useRef, type ReactNode, type RefObject } from 'react'

interface ResizableChart {
  resize: (width?: number, height?: number) => void
}

export interface ResponsiveChartFrameProps<T extends ResizableChart> {
  chartRef: RefObject<T | null>
  children: ReactNode
  className?: string
  resizeKey?: string | number
  variant?: 'home' | 'history'
}

const SIZE_EPSILON_PX = 1

export function ResponsiveChartFrame<T extends ResizableChart>({
  chartRef,
  children,
  className = '',
  resizeKey = 0,
  variant = 'history',
}: ResponsiveChartFrameProps<T>) {
  const frameRef = useRef<HTMLDivElement | null>(null)
  const previousSizeRef = useRef({ width: 0, height: 0 })
  const animationFrameRef = useRef<number | null>(null)

  const applySize = useCallback((width: number, height: number) => {
    if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) return

    const previous = previousSizeRef.current
    if (
      Math.abs(width - previous.width) < SIZE_EPSILON_PX
      && Math.abs(height - previous.height) < SIZE_EPSILON_PX
    ) return

    previousSizeRef.current = { width, height }
    if (animationFrameRef.current !== null) cancelAnimationFrame(animationFrameRef.current)
    animationFrameRef.current = requestAnimationFrame(() => {
      animationFrameRef.current = null
      chartRef.current?.resize(Math.round(width), Math.round(height))
    })
  }, [chartRef])

  const measure = useCallback(() => {
    const frame = frameRef.current
    if (!frame) return
    const rect = frame.getBoundingClientRect()
    applySize(rect.width, rect.height)
  }, [applySize])

  useLayoutEffect(() => {
    const frame = frameRef.current
    if (!frame) return

    const onVisibilityChange = () => {
      if (document.visibilityState === 'visible') measure()
    }

    let observer: ResizeObserver | undefined
    if (typeof ResizeObserver === 'function') {
      observer = new ResizeObserver((entries) => {
        const entry = entries[0]
        if (entry) applySize(entry.contentRect.width, entry.contentRect.height)
      })
      observer.observe(frame)
    } else {
      window.addEventListener('resize', measure)
    }

    document.addEventListener('visibilitychange', onVisibilityChange)
    measure()

    return () => {
      observer?.disconnect()
      window.removeEventListener('resize', measure)
      document.removeEventListener('visibilitychange', onVisibilityChange)
      if (animationFrameRef.current !== null) cancelAnimationFrame(animationFrameRef.current)
      animationFrameRef.current = null
    }
  }, [applySize, measure])

  useLayoutEffect(() => {
    measure()
  }, [measure, resizeKey])

  return (
    <div
      ref={frameRef}
      className={`chart-canvas ${variant}-chart ${className}`.trim()}
      data-chart-variant={variant}
      data-testid="responsive-chart-frame"
      aria-hidden="true"
    >
      {children}
    </div>
  )
}
