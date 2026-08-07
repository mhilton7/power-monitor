import { useEffect, useRef, type ReactNode, type RefObject } from 'react'
import { createPortal } from 'react-dom'

const focusableSelector = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

export function ModalLayer({
  children,
  onRequestClose,
  returnFocusRef,
}: {
  children: ReactNode
  onRequestClose: () => void
  returnFocusRef?: RefObject<HTMLElement | null>
}) {
  const contentRef = useRef<HTMLDivElement>(null)
  const closeRef = useRef(onRequestClose)
  // WebKit may clear :active focus before passive effects run after a click.
  // Capture the opener during the render that mounts the dialog so cleanup can
  // reliably return keyboard focus in every supported engine.
  const previousFocusRef = useRef<HTMLElement | null>(
    document.activeElement instanceof HTMLElement ? document.activeElement : null,
  )

  useEffect(() => {
    closeRef.current = onRequestClose
  }, [onRequestClose])

  useEffect(() => {
    const returnFocus = returnFocusRef?.current ?? previousFocusRef.current
    const previousOverflow = document.body.style.overflow
    const previousPaddingRight = document.body.style.paddingRight
    const scrollbarWidth = window.innerWidth - document.documentElement.clientWidth

    document.body.style.overflow = 'hidden'
    if (scrollbarWidth > 0) document.body.style.paddingRight = `${scrollbarWidth}px`

    const focusFrame = window.requestAnimationFrame(() => {
      const first = contentRef.current?.querySelector<HTMLElement>(focusableSelector)
      ;(first ?? contentRef.current)?.focus()
    })

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        closeRef.current()
        return
      }
      if (event.key !== 'Tab') return
      const controls = [...(contentRef.current?.querySelectorAll<HTMLElement>(focusableSelector) ?? [])]
        .filter((item) => item.offsetParent !== null)
      if (controls.length === 0) {
        event.preventDefault()
        contentRef.current?.focus()
        return
      }
      const first = controls[0]
      const last = controls.at(-1)
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last?.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first?.focus()
      }
    }

    document.addEventListener('keydown', onKeyDown)
    return () => {
      window.cancelAnimationFrame(focusFrame)
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = previousOverflow
      document.body.style.paddingRight = previousPaddingRight
      // Restore after the close-driven URL/state update commits. WebKit can
      // otherwise discard focus while the modal portal is being removed.
      window.requestAnimationFrame(() => {
        returnFocus?.focus({ preventScroll: true })
      })
    }
  }, [returnFocusRef])

  return createPortal(
    <div className="modal-layer">
      <button
        type="button"
        className="modal-layer-backdrop"
        aria-label="Close dialog"
        tabIndex={-1}
        onClick={onRequestClose}
      />
      <div ref={contentRef} className="modal-layer-content" tabIndex={-1}>
        {children}
      </div>
    </div>,
    document.body,
  )
}
