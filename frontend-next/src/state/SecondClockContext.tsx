import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'

interface SecondClockValue {
  nowMs: number
}

const SecondClockContext = createContext<SecondClockValue>({ nowMs: 0 })

/**
 * One application-wide browser clock for every elapsed-time label.
 *
 * This only updates presentation state. It never performs a request,
 * invalidates a query, or opens another event stream.
 */
export function SecondClockProvider({ children }: { children: ReactNode }) {
  const [nowMs, setNowMs] = useState(() => Date.now())

  useEffect(() => {
    const update = () => { setNowMs(Date.now()) }
    const timer = window.setInterval(update, 1_000)
    const onVisibility = () => {
      if (document.visibilityState === 'visible') update()
    }
    document.addEventListener('visibilitychange', onVisibility)
    window.addEventListener('focus', update)
    return () => {
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisibility)
      window.removeEventListener('focus', update)
    }
  }, [])

  const value = useMemo(() => ({ nowMs }), [nowMs])
  return (
    <SecondClockContext.Provider value={value}>
      {children}
    </SecondClockContext.Provider>
  )
}

export function useSecondClock(): number {
  return useContext(SecondClockContext).nowMs
}
