import { useQuery } from '@tanstack/react-query'
import { createContext, useContext, type ReactNode } from 'react'
import { resolveSingleHome } from '../api/adapters'
import { request } from '../api/client'
import type { HomeResolution } from '../types/models'
import { useAuth } from './AuthContext'

interface SingleHomeValue {
  resolution?: HomeResolution
  loading: boolean
  error: unknown
  refresh: () => Promise<unknown>
}

const SingleHomeContext = createContext<SingleHomeValue | undefined>(undefined)

export function SingleHomeProvider({ children }: { children: ReactNode }) {
  const { session } = useAuth()
  const home = useQuery({
    queryKey: ['single-home'],
    queryFn: () => request('/api/v1/sites', {}, resolveSingleHome),
    enabled: Boolean(session?.authenticated),
    staleTime: Number.POSITIVE_INFINITY,
    retry: 1,
  })
  return (
    <SingleHomeContext.Provider
      value={{
        resolution: home.data,
        loading: home.isLoading,
        error: home.error,
        refresh: home.refetch,
      }}
    >
      {children}
    </SingleHomeContext.Provider>
  )
}

export function useSingleHome(): SingleHomeValue {
  const value = useContext(SingleHomeContext)
  if (!value) throw new Error('SingleHomeProvider is missing')
  return value
}
