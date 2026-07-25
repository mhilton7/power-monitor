export interface RatePlanRemovalInput {
  planId: string
  expectedRevision: number
  dependencyToken: string
  confirmation: string
  reason: string
  permanentDraftDeletion: boolean
  idempotencyKey?: string
}

export function ratePlanRemovalRequest(input: RatePlanRemovalInput): {
  path: string
  method: 'DELETE' | 'POST'
  payload: Record<string, string | number>
} {
  const payload: Record<string, string | number> = {
    expected_revision: input.expectedRevision,
    expected_dependency_token: input.dependencyToken,
    confirmation: input.confirmation,
    reason: input.reason,
  }
  if (!input.permanentDraftDeletion) {
    payload.idempotency_key = input.idempotencyKey
      ?? `remove-${input.planId}-${crypto.randomUUID()}`
  }
  return {
    path: input.permanentDraftDeletion
      ? `/api/v1/admin/rate-plan-drafts/${input.planId}`
      : `/api/v1/admin/rate-plans/${input.planId}/remove`,
    method: input.permanentDraftDeletion ? 'DELETE' : 'POST',
    payload,
  }
}
