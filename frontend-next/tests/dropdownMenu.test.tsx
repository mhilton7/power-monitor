import { createPortal } from 'react-dom'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { Link, BrowserRouter } from '../src/app/router'
import { DropdownMenu, DropdownMenuItem } from '../src/components/overlays/DropdownMenu'

function Harness({ portalAction = false }: { portalAction?: boolean }) {
  return (
    <BrowserRouter>
      <div>
        <DropdownMenu label="First actions" trigger="First menu">
          <DropdownMenuItem onSelect={() => undefined}>First action</DropdownMenuItem>
          {portalAction && createPortal(
            <DropdownMenuItem onSelect={() => undefined}>Portal action</DropdownMenuItem>,
            document.body,
          )}
        </DropdownMenu>
        <DropdownMenu label="Second actions" trigger="Second menu">
          <DropdownMenuItem onSelect={() => undefined}>Second action</DropdownMenuItem>
        </DropdownMenu>
        <button type="button">Outside</button>
        <Link to="/history">Navigate</Link>
      </div>
    </BrowserRouter>
  )
}

describe('shared dropdown menu', () => {
  it('closes on pointer-away, Escape, another menu, route change, scroll, and resize', async () => {
    const user = userEvent.setup()
    vi.stubGlobal('scrollTo', vi.fn())
    render(<Harness />)
    const first = screen.getByRole('button', { name: 'First actions' })

    await user.click(first)
    expect(screen.getByRole('menu', { name: 'First actions' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Outside' }))
    expect(screen.queryByRole('menu', { name: 'First actions' })).not.toBeInTheDocument()

    await user.click(first)
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('menu', { name: 'First actions' })).not.toBeInTheDocument()
    await vi.waitFor(() => { expect(first).toHaveFocus() })

    await user.click(first)
    await user.click(screen.getByRole('button', { name: 'Second actions' }))
    expect(screen.queryByRole('menu', { name: 'First actions' })).not.toBeInTheDocument()
    expect(screen.getByRole('menu', { name: 'Second actions' })).toBeInTheDocument()

    await user.click(first)
    await user.click(screen.getByRole('link', { name: 'Navigate' }))
    await vi.waitFor(() => {
      expect(screen.queryByRole('menu', { name: 'First actions' })).not.toBeInTheDocument()
    })

    await user.click(first)
    await vi.waitFor(() => { expect(screen.getByRole('menu', { name: 'First actions' })).toBeInTheDocument() })
    window.dispatchEvent(new Event('resize'))
    await vi.waitFor(() => { expect(screen.queryByRole('menu', { name: 'First actions' })).not.toBeInTheDocument() })

    await user.click(first)
    await vi.waitFor(() => { expect(screen.getByRole('menu', { name: 'First actions' })).toBeInTheDocument() })
    window.dispatchEvent(new Event('scroll'))
    await vi.waitFor(() => { expect(screen.queryByRole('menu', { name: 'First actions' })).not.toBeInTheDocument() })
  })

  it('supports keyboard navigation, closes before actions, and accepts portal items', async () => {
    const user = userEvent.setup()
    const action = vi.fn()
    render(
      <BrowserRouter>
        <DropdownMenu label="Keyboard actions" trigger="Actions">
          <DropdownMenuItem onSelect={() => undefined}>Alpha</DropdownMenuItem>
          <DropdownMenuItem onSelect={action}>Bravo</DropdownMenuItem>
          {createPortal(
            <DropdownMenuItem onSelect={action}>Portal action</DropdownMenuItem>,
            document.body,
          )}
        </DropdownMenu>
      </BrowserRouter>,
    )
    const trigger = screen.getByRole('button', { name: 'Keyboard actions' })
    trigger.focus()
    await user.keyboard('{ArrowDown}')
    await vi.waitFor(() => { expect(screen.getByRole('menuitem', { name: 'Alpha' })).toHaveFocus() })
    await user.keyboard('{ArrowDown}')
    expect(screen.getByRole('menuitem', { name: 'Bravo' })).toHaveFocus()
    await user.keyboard('{End}')
    expect(screen.getByRole('menuitem', { name: 'Bravo' })).toHaveFocus()
    await user.keyboard('{Enter}')
    expect(screen.queryByRole('menu', { name: 'Keyboard actions' })).not.toBeInTheDocument()
    expect(action).toHaveBeenCalledTimes(1)

    await user.click(trigger)
    await user.click(screen.getByRole('menuitem', { name: 'Portal action' }))
    expect(screen.queryByRole('menu', { name: 'Keyboard actions' })).not.toBeInTheDocument()
    expect(action).toHaveBeenCalledTimes(2)
  })

  it('removes global listeners safely when the owner unmounts', async () => {
    const user = userEvent.setup()
    const view = render(<Harness />)
    await user.click(screen.getByRole('button', { name: 'First actions' }))
    view.unmount()
    expect(() => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
      document.dispatchEvent(new Event('pointerdown', { bubbles: true }))
    }).not.toThrow()
  })
})
