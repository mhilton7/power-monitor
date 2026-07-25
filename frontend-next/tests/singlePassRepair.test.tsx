import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ModalLayer } from '../src/components/overlays/ModalLayer'
import { newRateDraft, period, tier } from '../src/features/rates/rateDocument'

describe('single-pass repair contracts', () => {
  it('builds complete exact-string rate documents without browser number coercion', () => {
    const draft = newRateDraft({
      id: 'home-1',
      currency: 'USD',
      timezone: 'America/Los_Angeles',
    })
    draft.tiers = [tier(0, '0', '579.000'), tier(1, '579.000', null)]
    draft.seasons[0]?.schedules[0]?.periods.push(period('Overnight', 1260, 1440, '0.12345678', 3))

    expect(draft.schema_version).toBe('power-monitor-rate-plan/1.0')
    expect(draft.tiers[0]?.upper_bound_exclusive_kwh).toBe('579.000')
    expect(draft.seasons[0]?.schedules[0]?.periods.at(-1)?.price_per_kwh).toBe('0.12345678')
    expect(typeof draft.seasons[0]?.schedules[0]?.periods.at(-1)?.price_per_kwh).toBe('string')
  })

  it('renders dialog content above its backdrop and restores focus on Escape', async () => {
    const close = vi.fn()
    const user = userEvent.setup()
    render(<><button type="button">Opener</button><ModalLayer onRequestClose={close}><section role="dialog" aria-label="Importer"><button type="button">First control</button></section></ModalLayer></>)

    expect(screen.getByRole('dialog', { name: 'Importer' })).toBeVisible()
    expect(document.body.style.overflow).toBe('hidden')
    await user.keyboard('{Escape}')
    expect(close).toHaveBeenCalledOnce()
  })
})
