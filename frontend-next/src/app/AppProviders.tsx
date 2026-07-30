import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode, type ReactNode } from 'react'
import { BrowserRouter } from './router'
import { AppearanceProvider } from '../state/AppearanceContext'
import { AuthProvider } from '../state/AuthContext'
import { SecondClockProvider } from '../state/SecondClockContext'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, staleTime: 10_000, refetchOnWindowFocus: false },
    mutations: { retry: false },
  },
})

export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <AppearanceProvider>
          <BrowserRouter>
            <SecondClockProvider>
              <AuthProvider>{children}</AuthProvider>
            </SecondClockProvider>
          </BrowserRouter>
        </AppearanceProvider>
      </QueryClientProvider>
    </StrictMode>
  )
}
