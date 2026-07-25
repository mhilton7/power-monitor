import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'

export type ThemePreference = 'dark' | 'light' | 'system'
export type Density = 'comfortable' | 'compact'

interface AppearanceValue {
  theme: ThemePreference
  density: Density
  accent: string
  railCollapsed: boolean
  showSensorsCard: boolean
  showDailyChart: boolean
  setTheme: (theme: ThemePreference) => void
  setDensity: (density: Density) => void
  setAccent: (accent: string) => void
  setRailCollapsed: (collapsed: boolean) => void
  setShowSensorsCard: (visible: boolean) => void
  setShowDailyChart: (visible: boolean) => void
}

const AppearanceContext = createContext<AppearanceValue | undefined>(undefined)

function stored<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  const value = localStorage.getItem(key)
  return value && allowed.includes(value as T) ? value as T : fallback
}

export function AppearanceProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<ThemePreference>(() => stored('pm-theme', ['dark', 'light', 'system'], 'dark'))
  const [density, setDensityState] = useState<Density>(() => stored('pm-density', ['comfortable', 'compact'], 'comfortable'))
  const [accent, setAccentState] = useState(() => localStorage.getItem('pm-accent') ?? '#78dfbf')
  const [railCollapsed, setRailCollapsedState] = useState(() => localStorage.getItem('pm-rail-collapsed') === 'true')
  const [showSensorsCard, setShowSensorsCard] = useState(() => localStorage.getItem('pm-show-sensors-card') !== 'false')
  const [showDailyChart, setShowDailyChart] = useState(() => localStorage.getItem('pm-show-daily-chart') !== 'false')

  useEffect(() => {
    const root = document.documentElement
    const resolved = theme === 'system'
      ? window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
      : theme
    root.dataset.theme = resolved
    root.dataset.density = density
    root.style.setProperty('--accent', accent)
    localStorage.setItem('pm-theme', theme)
    localStorage.setItem('pm-density', density)
    localStorage.setItem('pm-accent', accent)
    localStorage.setItem('pm-rail-collapsed', String(railCollapsed))
    localStorage.setItem('pm-show-sensors-card', String(showSensorsCard))
    localStorage.setItem('pm-show-daily-chart', String(showDailyChart))
  }, [accent, density, railCollapsed, showDailyChart, showSensorsCard, theme])

  const value = useMemo<AppearanceValue>(() => ({
    theme,
    density,
    accent,
    railCollapsed,
    showSensorsCard,
    showDailyChart,
    setTheme: setThemeState,
    setDensity: setDensityState,
    setAccent: setAccentState,
    setRailCollapsed: setRailCollapsedState,
    setShowSensorsCard,
    setShowDailyChart,
  }), [accent, density, railCollapsed, showDailyChart, showSensorsCard, theme])

  return <AppearanceContext.Provider value={value}>{children}</AppearanceContext.Provider>
}

export function useAppearance(): AppearanceValue {
  const value = useContext(AppearanceContext)
  if (!value) throw new Error('AppearanceProvider is missing')
  return value
}
