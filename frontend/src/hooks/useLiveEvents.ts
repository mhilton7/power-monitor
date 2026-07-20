import { useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'

export function useLiveEvents(siteId?: string) {
  const queryClient = useQueryClient()
  useEffect(() => {
    let fallback: number | undefined
    let source: EventSource | undefined
    const connect = () => {
      source = new EventSource(`/api/v1/events/stream${siteId ? `?site_id=${encodeURIComponent(siteId)}` : ''}`)
      source.addEventListener('fleet', () => {
        void queryClient.invalidateQueries({ queryKey: ['fleet'] })
        void queryClient.invalidateQueries({ queryKey: ['devices'] })
      })
      source.onerror = () => {
        source?.close()
        if (!fallback) {
          fallback = window.setInterval(() => {
            void queryClient.invalidateQueries({ queryKey: ['fleet'] })
            void queryClient.invalidateQueries({ queryKey: ['devices'] })
          }, 15_000)
        }
      }
    }
    connect()
    return () => {
      source?.close()
      if (fallback) window.clearInterval(fallback)
    }
  }, [queryClient, siteId])
}

