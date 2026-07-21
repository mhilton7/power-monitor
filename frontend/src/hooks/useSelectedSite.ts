import { useEffect, useState } from 'react'

export function useSelectedSiteId(): string | undefined {
  const [siteId, setSiteId] = useState(() => localStorage.getItem('pm-site-id') ?? undefined)
  useEffect(() => {
    const update = (event: Event) => {
      setSiteId((event as CustomEvent<string>).detail || undefined)
    }
    window.addEventListener('pm-site-scope-changed', update)
    return () => { window.removeEventListener('pm-site-scope-changed', update) }
  }, [])
  return siteId
}
