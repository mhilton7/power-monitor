import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ModalLayer } from '../src/components/overlays/ModalLayer'
import { ApiError, errorMessage } from '../src/api/client'
import { ratePlanRemovalRequest } from '../src/features/rates/lifecycle'
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

  it('omits lifecycle-only idempotency fields when permanently deleting an unused draft', () => {
    const draftDelete = ratePlanRemovalRequest({
      planId: 'draft-1',
      expectedRevision: 2,
      dependencyToken: 'a'.repeat(64),
      confirmation: 'DRAFT-1',
      reason: 'Discard reviewed unused draft',
      permanentDraftDeletion: true,
      idempotencyKey: 'must-not-be-sent',
    })
    expect(draftDelete).toMatchObject({
      path: '/api/v1/admin/rate-plan-drafts/draft-1',
      method: 'DELETE',
      payload: {
        expected_revision: 2,
        expected_dependency_token: 'a'.repeat(64),
        confirmation: 'DRAFT-1',
        reason: 'Discard reviewed unused draft',
      },
    })
    expect(draftDelete.payload).not.toHaveProperty('idempotency_key')

    const softRemove = ratePlanRemovalRequest({
      planId: 'published-1',
      expectedRevision: 3,
      dependencyToken: 'b'.repeat(64),
      confirmation: 'PUBLISHED-1',
      reason: 'Retire reviewed published plan',
      permanentDraftDeletion: false,
      idempotencyKey: 'remove-published-1',
    })
    expect(softRemove.payload).toHaveProperty('idempotency_key', 'remove-published-1')
  })

  it('shows the rejected field when the server returns structured validation details', () => {
    const error = new ApiError({
      title: 'Request validation failed',
      detail: 'One or more fields are invalid',
      status: 422,
      code: 'validation_error',
      errors: [{
        location: ['body', 'idempotency_key'],
        message: 'Extra inputs are not permitted',
      }],
    })
    expect(errorMessage(error)).toBe(
      'One or more fields are invalid: idempotency_key: Extra inputs are not permitted',
    )
  })
})
