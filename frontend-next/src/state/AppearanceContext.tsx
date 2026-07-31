import { useQuery, useQueryClient } from '@tanstack/react-query'
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { request } from '../api/client'
import { useAuth } from './AuthContext'

export type ThemePreference = 'dark' | 'light' | 'system'
export type Density = 'comfortable' | 'compact'
export type ChartColorKind = 'power' | 'energy' | 'cost'

export const DEFAULT_CHART_COLORS: Record<ChartColorKind, string> = {
  power: '#78DFBF',
  energy: '#78DFBF',
  cost: '#C9A7FF',
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

interface PublishedAppearance {
  chartColors: Record<ChartColorKind, string>
  revision: number
  updatedAt: string
}

function adaptPublishedAppearance(value: unknown): PublishedAppearance {
  if (!value || typeof value !== 'object') throw new Error('Invalid appearance response')
  const item = value as Record<string, unknown>
  const revision = Number(item.revision)
  const updatedAt = typeof item.updated_at === 'string' ? item.updated_at : ''
  const power = item.chart_power_color
  const energy = item.chart_energy_color
  const cost = item.chart_cost_color
  if (!Number.isInteger(revision) || revision < 1 || !updatedAt || typeof power !== 'string' || typeof energy !== 'string' || typeof cost !== 'string') throw new Error('Invalid appearance response')
  return {
    chartColors: {
      power: normalizeChartColor(power, DEFAULT_CHART_COLORS.power),
      energy: normalizeChartColor(energy, DEFAULT_CHART_COLORS.energy),
      cost: normalizeChartColor(cost, DEFAULT_CHART_COLORS.cost),
    },
    revision,
    updatedAt,
  }
}

interface AppearanceValue {
  theme: ThemePreference
  density: Density
  accent: string
  railCollapsed: boolean
  showSensorsCard: boolean
  showDailyChart: boolean
  chartColors: Record<ChartColorKind, string>
  chartColorRevision: number
  chartColorsLoading: boolean
  setTheme: (theme: ThemePreference) => void
  setDensity: (density: Density) => void
  setAccent: (accent: string) => void
  setRailCollapsed: (collapsed: boolean) => void
  setShowSensorsCard: (visible: boolean) => void
  setShowDailyChart: (visible: boolean) => void
  publishChartColors: (colors: Record<ChartColorKind, string>) => Promise<void>
}

const AppearanceContext = createContext<AppearanceValue | undefined>(undefined)

function stored<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  const value = localStorage.getItem(key)
  return value && allowed.includes(value as T) ? value as T : fallback
}

export function AppearanceProvider({ children }: { children: ReactNode }) {
  const { session } = useAuth()
  const client = useQueryClient()
  const [theme, setThemeState] = useState<ThemePreference>(() => stored('pm-theme', ['dark', 'light', 'system'], 'dark'))
  const [density, setDensityState] = useState<Density>(() => stored('pm-density', ['comfortable', 'compact'], 'comfortable'))
  const [accent, setAccentState] = useState(() => localStorage.getItem('pm-accent') ?? '#78dfbf')
  const [railCollapsed, setRailCollapsedState] = useState(() => localStorage.getItem('pm-rail-collapsed') === 'true')
  const [showSensorsCard, setShowSensorsCard] = useState(() => localStorage.getItem('pm-show-sensors-card') !== 'false')
  const [showDailyChart, setShowDailyChart] = useState(() => localStorage.getItem('pm-show-daily-chart') !== 'false')
  const published = useQuery({
    queryKey: ['dashboard-appearance'],
    queryFn: () => request('/api/v1/appearance', {}, adaptPublishedAppearance),
    enabled: session?.authenticated === true,
    staleTime: 30_000,
  })
  const chartColors = published.data?.chartColors ?? DEFAULT_CHART_COLORS

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
    chartColors,
    chartColorRevision: published.data?.revision ?? 1,
    chartColorsLoading: published.isLoading,
    setTheme: setThemeState,
    setDensity: setDensityState,
    setAccent: setAccentState,
    setRailCollapsed: setRailCollapsedState,
    setShowSensorsCard,
    setShowDailyChart,
    publishChartColors: async (colors) => {
      const result = await request('/api/v1/appearance', {
        method: 'PUT',
        body: JSON.stringify({
          chart_power_color: normalizeChartColor(colors.power, DEFAULT_CHART_COLORS.power),
          chart_energy_color: normalizeChartColor(colors.energy, DEFAULT_CHART_COLORS.energy),
          chart_cost_color: normalizeChartColor(colors.cost, DEFAULT_CHART_COLORS.cost),
          expected_revision: published.data?.revision ?? 1,
        }),
      }, adaptPublishedAppearance)
      client.setQueryData(['dashboard-appearance'], result)
    },
  }), [accent, chartColors, client, density, published.data?.revision, published.isLoading, railCollapsed, showDailyChart, showSensorsCard, theme])

  return <AppearanceContext.Provider value={value}>{children}</AppearanceContext.Provider>
}

export function useAppearance(): AppearanceValue {
  const value = useContext(AppearanceContext)
  if (!value) throw new Error('AppearanceProvider is missing')
  return value
}
