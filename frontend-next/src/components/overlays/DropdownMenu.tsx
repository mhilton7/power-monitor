import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from 'react'
import { useLocation } from '../../app/router'
import type { DropdownAction } from '../../types/models'

let activeMenu: { id: symbol; close: () => void } | undefined

interface MenuContextValue {
  ownerId: string
  closeAndRun: (action?: () => void) => void
}

const MenuContext = createContext<MenuContextValue | undefined>(undefined)

export function DropdownMenu({
  label,
  trigger,
  triggerClassName = 'button ghost',
  menuClassName = 'menu-popover',
  children,
}: {
  label: string
  trigger: ReactNode
  triggerClassName?: string
  menuClassName?: string
  children: ReactNode
}) {
  const [open, setOpen] = useState(false)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const focusFrameRef = useRef<number | null>(null)
  const menuId = useRef(Symbol('dropdown-menu'))
  const id = useId()
  const location = useLocation()
  const locationKey = `${location.pathname}${location.search}${location.hash}`
  const previousLocationKey = useRef(locationKey)

  const close = useCallback((returnFocus = false) => {
    if (focusFrameRef.current !== null) {
      window.cancelAnimationFrame(focusFrameRef.current)
      focusFrameRef.current = null
    }
    setOpen(false)
    if (activeMenu?.id === menuId.current) activeMenu = undefined
    if (returnFocus) {
      focusFrameRef.current = window.requestAnimationFrame(() => {
        focusFrameRef.current = null
        triggerRef.current?.focus()
      })
    }
  }, [])
  const openMenu = (focus: 'first' | 'last' | 'none' = 'none') => {
    activeMenu?.close()
    setOpen(true)
    activeMenu = { id: menuId.current, close: () => { close(false) } }
    if (focus !== 'none') {
      focusFrameRef.current = window.requestAnimationFrame(() => {
        focusFrameRef.current = null
        const items = menuItems(menuRef.current)
        ;(focus === 'last' ? items.at(-1) : items[0])?.focus()
      })
    }
  }

  useEffect(() => {
    if (!open) return
    const isInside = (event: Event) => {
      const path = event.composedPath()
      return path.includes(triggerRef.current as EventTarget)
        || path.includes(menuRef.current as EventTarget)
        || path.some((item) => (
          item instanceof HTMLElement && item.dataset.dropdownOwner === id
        ))
    }
    const onPointerDown = (event: PointerEvent) => {
      if (!isInside(event)) close(false)
    }
    const onFocusIn = (event: FocusEvent) => {
      if (!isInside(event)) close(false)
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        close(true)
      }
    }
    const onViewportChange = () => { close(false) }
    document.addEventListener('pointerdown', onPointerDown, true)
    document.addEventListener('focusin', onFocusIn, true)
    document.addEventListener('keydown', onKeyDown)
    window.addEventListener('scroll', onViewportChange, true)
    window.addEventListener('resize', onViewportChange)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown, true)
      document.removeEventListener('focusin', onFocusIn, true)
      document.removeEventListener('keydown', onKeyDown)
      window.removeEventListener('scroll', onViewportChange, true)
      window.removeEventListener('resize', onViewportChange)
    }
  }, [close, id, open])

  useEffect(() => {
    if (previousLocationKey.current === locationKey) return
    previousLocationKey.current = locationKey
    close(false)
  }, [close, locationKey])

  useEffect(() => () => {
    if (activeMenu?.id === menuId.current) activeMenu = undefined
    if (focusFrameRef.current !== null) window.cancelAnimationFrame(focusFrameRef.current)
  }, [])

  const onTriggerKeyDown = (event: ReactKeyboardEvent<HTMLButtonElement>) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      openMenu(event.key === 'ArrowUp' ? 'last' : 'first')
    }
  }
  const onMenuKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    const items = menuItems(menuRef.current)
    const index = items.indexOf(document.activeElement as HTMLButtonElement)
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      const direction = event.key === 'ArrowDown' ? 1 : -1
      items[(index + direction + items.length) % items.length]?.focus()
    } else if (event.key === 'Home') {
      event.preventDefault()
      items[0]?.focus()
    } else if (event.key === 'End') {
      event.preventDefault()
      items.at(-1)?.focus()
    }
  }
  const context: MenuContextValue = {
    ownerId: id,
    closeAndRun: (action) => {
      close(false)
      if (action) queueMicrotask(action)
    },
  }
  return (
    <div className="dropdown-menu">
      <button
        ref={triggerRef}
        type="button"
        className={triggerClassName}
        aria-label={label}
        aria-haspopup="menu"
        aria-controls={open ? id : undefined}
        aria-expanded={open}
        onClick={() => { if (open) close(false); else openMenu('first') }}
        onKeyDown={onTriggerKeyDown}
      >
        {trigger}
      </button>
      {open && (
        <MenuContext.Provider value={context}>
          <div
            ref={menuRef}
            id={id}
            className={menuClassName}
            role="menu"
            aria-label={label}
            onKeyDown={onMenuKeyDown}
          >
            {children}
          </div>
        </MenuContext.Provider>
      )}
    </div>
  )
}

export function DropdownMenuItem({
  onSelect,
  actionId,
  children,
  ...props
}: Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'onClick' | 'type'> & {
  onSelect: () => void
  actionId?: DropdownAction
  children: ReactNode
}) {
  const context = useContext(MenuContext)
  if (!context) throw new Error('DropdownMenuItem must be rendered inside DropdownMenu')
  return (
    <button
      {...props}
      type="button"
      role="menuitem"
      data-dropdown-owner={context.ownerId}
      data-canonical-action={actionId}
      tabIndex={-1}
      onClick={() => { context.closeAndRun(onSelect) }}
    >
      {children}
    </button>
  )
}

function menuItems(container: HTMLElement | null): HTMLButtonElement[] {
  return [...(container?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not([disabled])') ?? [])]
}
