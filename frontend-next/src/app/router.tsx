import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type AnchorHTMLAttributes,
  type MouseEvent,
  type ReactNode,
} from 'react'

export interface BrowserLocation {
  pathname: string
  search: string
  hash: string
  state: unknown
}

interface NavigateOptions {
  replace?: boolean
}

interface RouterValue {
  location: BrowserLocation
  navigate: (to: string, options?: NavigateOptions) => void
}

const RouterContext = createContext<RouterValue | undefined>(undefined)

function currentLocation(): BrowserLocation {
  return {
    pathname: window.location.pathname,
    search: window.location.search,
    hash: window.location.hash,
    state: window.history.state as unknown,
  }
}

export function BrowserRouter({ children }: { children: ReactNode }) {
  const [location, setLocation] = useState(currentLocation)
  useEffect(() => {
    const update = () => { setLocation(currentLocation()) }
    window.addEventListener('popstate', update)
    return () => { window.removeEventListener('popstate', update) }
  }, [])
  const value = useMemo<RouterValue>(() => ({
    location,
    navigate: (to, options) => {
      const target = new URL(to, window.location.origin)
      if (target.origin !== window.location.origin) throw new Error('Navigation must remain on this server.')
      const next = `${target.pathname}${target.search}${target.hash}`
      window.history[options?.replace ? 'replaceState' : 'pushState']({}, '', next)
      setLocation(currentLocation())
      window.scrollTo({ top: 0, behavior: 'auto' })
    },
  }), [location])
  return <RouterContext.Provider value={value}>{children}</RouterContext.Provider>
}

export function useLocation(): BrowserLocation {
  const value = useContext(RouterContext)
  if (!value) throw new Error('BrowserRouter is missing')
  return value.location
}

export function useNavigate(): RouterValue['navigate'] {
  const value = useContext(RouterContext)
  if (!value) throw new Error('BrowserRouter is missing')
  return value.navigate
}

export function Navigate({ to, replace = false }: { to: string; replace?: boolean }) {
  const navigate = useNavigate()
  useEffect(() => { navigate(to, { replace }) }, [navigate, replace, to])
  return null
}

interface LinkProps extends Omit<AnchorHTMLAttributes<HTMLAnchorElement>, 'href'> {
  to: string
}

export function Link({ to, onClick, ...props }: LinkProps) {
  const navigate = useNavigate()
  const follow = (event: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(event)
    if (
      event.defaultPrevented
      || event.button !== 0
      || event.metaKey
      || event.ctrlKey
      || event.shiftKey
      || event.altKey
      || props.target === '_blank'
    ) return
    event.preventDefault()
    navigate(to)
  }
  return <a {...props} href={to} onClick={follow} />
}

interface NavLinkProps extends Omit<LinkProps, 'className'> {
  className?: string | ((state: { isActive: boolean }) => string | undefined)
}

export function NavLink({ className, to, ...props }: NavLinkProps) {
  const location = useLocation()
  const target = new URL(to, window.location.origin)
  const isActive = location.pathname === target.pathname
    || (target.pathname !== '/' && location.pathname.startsWith(`${target.pathname}/`))
  const resolved = typeof className === 'function' ? className({ isActive }) : className
  return <Link {...props} to={to} className={resolved} aria-current={isActive ? 'page' : undefined} />
}

export function useSearchParams(): [
  URLSearchParams,
  (params: URLSearchParams, options?: NavigateOptions) => void,
] {
  const location = useLocation()
  const navigate = useNavigate()
  const params = useMemo(() => new URLSearchParams(location.search), [location.search])
  return [
    params,
    (next, options) => {
      const query = next.toString()
      navigate(`${location.pathname}${query ? `?${query}` : ''}${location.hash}`, options)
    },
  ]
}
