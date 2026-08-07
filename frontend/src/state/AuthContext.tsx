import { useQuery, useQueryClient } from '@tanstack/react-query'
import { createContext, useContext, useEffect, useRef, type ReactNode } from 'react'
import { adaptSession } from '../api/adapters'
import { request } from '../api/client'
import type { UserSession } from '../types/models'

interface AuthValue {
  session?: UserSession
  loading: boolean
  error: unknown
  refresh: () => Promise<void>
}

const AuthContext = createContext<AuthValue | undefined>(undefined)

export function AuthProvider({ children }: { children: ReactNode }) {
  const client = useQueryClient()
  const session = useQuery({
    queryKey: ['session'],
    queryFn: () => request('/api/v1/auth/session', {}, adaptSession),
    retry: false,
    staleTime: 30_000,
  })
  const boundary = session.data?.user
    ? `${session.data.user.id}:${session.data.user.accessRevision}`
    : session.data?.authenticated === false ? 'anonymous' : undefined
  const previousBoundary = useRef<string | undefined>(undefined)
  useEffect(() => {
    if (!boundary) return
    if (previousBoundary.current && previousBoundary.current !== boundary) {
      void client.cancelQueries({ predicate: (query) => query.queryKey[0] !== 'session' })
      client.removeQueries({ predicate: (query) => query.queryKey[0] !== 'session' })
    }
    previousBoundary.current = boundary
  }, [boundary, client])
  return (
    <AuthContext.Provider
      value={{
        session: session.data,
        loading: session.isLoading,
        error: session.error,
        refresh: async () => {
          await client.invalidateQueries({ queryKey: ['session'] })
        },
      }}
    >
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext)
  if (!value) throw new Error('AuthProvider is missing')
  return value
}
