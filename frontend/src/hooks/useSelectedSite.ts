import { useEffect, useState } from 'react'

export function useSelectedSiteId(): string | undefined {
  const [siteId, setSiteId] = useState(() => {
    try {
      return window.localStorage.getItem('pm-site-id') ?? undefined
    } catch {
      return undefined
    }
  })
  useEffect(() => {
    const update = (event: Event) => {
      setSiteId((event as CustomEvent<string>).detail || undefined)
    }
    window.addEventListener('pm-site-scope-changed', update)
    return () => { window.removeEventListener('pm-site-scope-changed', update) }
  }, [])
  return siteId
}
