import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'

export type ThemePreference = 'dark' | 'light' | 'system'
export type Density = 'comfortable' | 'compact'
export type ChartColorKind = 'power' | 'energy' | 'cost'

export const DEFAULT_CHART_COLORS: Record<ChartColorKind, string> = {
  power: '#78DFBF',
  energy: '#78DFBF',
  cost: '#C9A7FF',
}

const CHART_COLOR_KEYS: Record<ChartColorKind, string> = {
  power: 'pm-chart-power-color',
  energy: 'pm-chart-energy-color',
  cost: 'pm-chart-cost-color',
}

export function normalizeChartColor(value: string, fallback: string): string {
  return /^#[0-9a-f]{6}$/iu.test(value) ? value.toUpperCase() : fallback
}

export function chartColorContrast(foreground: string, background: string): number {
  const luminance = (color: string) => {
    const normalized = normalizeChartColor(color, '#000000').slice(1)
    const channels = [0, 2, 4].map((offset) => Number.parseInt(normalized.slice(offset, offset + 2), 16) / 255)
      .map((channel) => channel <= .04045 ? channel / 12.92 : ((channel + .055) / 1.055) ** 2.4)
    return .2126 * (channels[0] ?? 0) + .7152 * (channels[1] ?? 0) + .0722 * (channels[2] ?? 0)
  }
  const first = luminance(foreground)
  const second = luminance(background)
  return (Math.max(first, second) + .05) / (Math.min(first, second) + .05)
}

function storedChartColor(kind: ChartColorKind): string {
  return normalizeChartColor(localStorage.getItem(CHART_COLOR_KEYS[kind]) ?? '', DEFAULT_CHART_COLORS[kind])
}

interface AppearanceValue {
  theme: ThemePreference
  density: Density
  accent: string
  railCollapsed: boolean
  showSensorsCard: boolean
  showDailyChart: boolean
  chartColors: Record<ChartColorKind, string>
  setTheme: (theme: ThemePreference) => void
  setDensity: (density: Density) => void
  setAccent: (accent: string) => void
  setRailCollapsed: (collapsed: boolean) => void
  setShowSensorsCard: (visible: boolean) => void
  setShowDailyChart: (visible: boolean) => void
  setChartColor: (kind: ChartColorKind, color: string) => void
  resetChartColors: () => void
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
  const [chartColors, setChartColors] = useState<Record<ChartColorKind, string>>(() => ({
    power: storedChartColor('power'),
    energy: storedChartColor('energy'),
    cost: storedChartColor('cost'),
  }))

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
    for (const kind of Object.keys(CHART_COLOR_KEYS) as ChartColorKind[]) {
      localStorage.setItem(CHART_COLOR_KEYS[kind], chartColors[kind])
    }
  }, [accent, chartColors, density, railCollapsed, showDailyChart, showSensorsCard, theme])

  const value = useMemo<AppearanceValue>(() => ({
    theme,
    density,
    accent,
    railCollapsed,
    showSensorsCard,
    showDailyChart,
    chartColors,
    setTheme: setThemeState,
    setDensity: setDensityState,
    setAccent: setAccentState,
    setRailCollapsed: setRailCollapsedState,
    setShowSensorsCard,
    setShowDailyChart,
    setChartColor: (kind, color) => {
      if (!/^#[0-9a-f]{6}$/iu.test(color)) return
      setChartColors((current) => ({ ...current, [kind]: color.toUpperCase() }))
    },
    resetChartColors: () => { setChartColors({ ...DEFAULT_CHART_COLORS }); },
  }), [accent, chartColors, density, railCollapsed, showDailyChart, showSensorsCard, theme])

  return <AppearanceContext.Provider value={value}>{children}</AppearanceContext.Provider>
}

export function useAppearance(): AppearanceValue {
  const value = useContext(AppearanceContext)
  if (!value) throw new Error('AppearanceProvider is missing')
  return value
}
