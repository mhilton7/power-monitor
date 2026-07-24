import {
  cloneElement,
  createContext,
  isValidElement,
  useContext,
  useEffect,
  useRef,
  type ReactElement,
  type ReactNode,
} from 'react'

export type ActionSurface =
  | 'workspace_header'
  | 'page'
  | 'panel_header'
  | 'empty_state'
  | 'resource_row'
  | 'resource_detail'
  | 'overflow_menu'
  | 'dialog'
  | 'sticky_footer'
  | 'contextual_link'

export type CanonicalActionId =
  | 'utility_account.create'
  | 'utility_account.manage'
  | 'utility_account.recalculate'
  | 'billing_statement.import'
  | 'rate_plan.create_custom'
  | 'rate_plan.import_from_bill'
  | 'rate_plan.clone'
  | 'rate_plan.remove'
  | 'rate_plan.restore'
  | 'rate_source.create'
  | 'rate_source.check'
  | 'device.enroll'
  | 'user.add'
  | 'user.disable'
  | 'user.remove'
  | 'backup.create'
  | 'logs.export'
  | 'interface_text.save_draft'
  | 'interface_text.publish'
  | 'status_layout.save_draft'
  | 'status_layout.publish'
  | 'site.create'
  | 'site.view'
  | 'site.edit'
  | 'site.set_default'
  | 'site.disable'
  | 'site.enable'
  | 'site.remove'
  | 'site.restore'
  | 'site.transfer_resources'
  | 'site.view_audit'

export interface CanonicalActionDefinition {
  id: CanonicalActionId
  label: string
  route?: string
  workspace: 'overview' | 'monitoring' | 'analytics' | 'billing' | 'alerts' | 'administration'
  permission: string
  allowedSurfaces: ActionSurface[]
  presentation: 'primary' | 'secondary' | 'menu'
  contextualLinks: boolean
  allowMultiple: boolean
  auditIdentity: string
}

const define = (
  value: Omit<CanonicalActionDefinition, 'auditIdentity'> & { auditIdentity?: string },
): CanonicalActionDefinition => ({ ...value, auditIdentity: value.auditIdentity ?? value.id })

export const ACTION_REGISTRY: Record<CanonicalActionId, CanonicalActionDefinition> = {
  'utility_account.create': define({ id: 'utility_account.create', label: 'Create utility account', route: '/billing/accounts', workspace: 'billing', permission: 'utility_accounts.manage', allowedSurfaces: ['workspace_header', 'panel_header'], presentation: 'primary', contextualLinks: true, allowMultiple: false }),
  'utility_account.manage': define({ id: 'utility_account.manage', label: 'Manage account', workspace: 'billing', permission: 'utility_accounts.manage', allowedSurfaces: ['resource_row', 'resource_detail'], presentation: 'secondary', contextualLinks: false, allowMultiple: true }),
  'utility_account.recalculate': define({ id: 'utility_account.recalculate', label: 'Recalculate costs', workspace: 'billing', permission: 'costs.recalculate', allowedSurfaces: ['resource_row', 'resource_detail', 'overflow_menu'], presentation: 'secondary', contextualLinks: false, allowMultiple: true }),
  'billing_statement.import': define({ id: 'billing_statement.import', label: 'Import billing statement', workspace: 'billing', permission: 'utility_bills.manage', allowedSurfaces: ['resource_detail', 'overflow_menu'], presentation: 'menu', contextualLinks: false, allowMultiple: true }),
  'rate_plan.create_custom': define({ id: 'rate_plan.create_custom', label: 'Create custom plan', route: '/billing/rate-plans/new', workspace: 'billing', permission: 'rates.manage_custom', allowedSurfaces: ['workspace_header', 'panel_header'], presentation: 'primary', contextualLinks: true, allowMultiple: false }),
  'rate_plan.import_from_bill': define({ id: 'rate_plan.import_from_bill', label: 'Import rate plan from bill', route: '/billing/rate-plans/new?bill_import=open', workspace: 'billing', permission: 'utility_bills.manage', allowedSurfaces: ['resource_detail'], presentation: 'secondary', contextualLinks: false, allowMultiple: false }),
  'rate_plan.clone': define({ id: 'rate_plan.clone', label: 'Clone plan', workspace: 'billing', permission: 'rates.manage_custom', allowedSurfaces: ['resource_row', 'overflow_menu'], presentation: 'secondary', contextualLinks: false, allowMultiple: true }),
  'rate_plan.remove': define({ id: 'rate_plan.remove', label: 'Remove rate plan', workspace: 'billing', permission: 'rates.remove', allowedSurfaces: ['resource_row', 'dialog'], presentation: 'menu', contextualLinks: false, allowMultiple: true }),
  'rate_plan.restore': define({ id: 'rate_plan.restore', label: 'Restore rate plan', workspace: 'billing', permission: 'rates.restore', allowedSurfaces: ['resource_row', 'dialog'], presentation: 'secondary', contextualLinks: false, allowMultiple: true }),
  'rate_source.create': define({ id: 'rate_source.create', label: 'Add rate source', workspace: 'billing', permission: 'rates.manage_sources', allowedSurfaces: ['panel_header'], presentation: 'primary', contextualLinks: false, allowMultiple: false }),
  'rate_source.check': define({ id: 'rate_source.check', label: 'Check rate sources now', workspace: 'billing', permission: 'rates.check_sources', allowedSurfaces: ['workspace_header', 'panel_header'], presentation: 'secondary', contextualLinks: false, allowMultiple: false }),
  'device.enroll': define({ id: 'device.enroll', label: 'Enroll device', route: '/monitoring/enrollment', workspace: 'monitoring', permission: 'enrollment.manage', allowedSurfaces: ['workspace_header', 'page', 'contextual_link'], presentation: 'primary', contextualLinks: true, allowMultiple: false }),
  'user.add': define({ id: 'user.add', label: 'Add user', workspace: 'administration', permission: 'users.manage', allowedSurfaces: ['workspace_header', 'panel_header'], presentation: 'primary', contextualLinks: false, allowMultiple: false }),
  'user.disable': define({ id: 'user.disable', label: 'Disable user', workspace: 'administration', permission: 'users.disable', allowedSurfaces: ['resource_row', 'resource_detail', 'dialog'], presentation: 'secondary', contextualLinks: false, allowMultiple: true }),
  'user.remove': define({ id: 'user.remove', label: 'Remove user', workspace: 'administration', permission: 'users.remove', allowedSurfaces: ['resource_row', 'resource_detail', 'dialog'], presentation: 'menu', contextualLinks: false, allowMultiple: true }),
  'backup.create': define({ id: 'backup.create', label: 'Create backup', workspace: 'administration', permission: 'backups.create', allowedSurfaces: ['workspace_header', 'panel_header'], presentation: 'primary', contextualLinks: false, allowMultiple: false }),
  'logs.export': define({ id: 'logs.export', label: 'Export logs', workspace: 'administration', permission: 'logs.export', allowedSurfaces: ['panel_header'], presentation: 'secondary', contextualLinks: false, allowMultiple: false }),
  'interface_text.save_draft': define({ id: 'interface_text.save_draft', label: 'Save text draft', workspace: 'administration', permission: 'interface_text.manage', allowedSurfaces: ['sticky_footer'], presentation: 'secondary', contextualLinks: false, allowMultiple: false }),
  'interface_text.publish': define({ id: 'interface_text.publish', label: 'Publish interface text', workspace: 'administration', permission: 'interface_text.manage', allowedSurfaces: ['sticky_footer', 'dialog'], presentation: 'primary', contextualLinks: false, allowMultiple: false }),
  'status_layout.save_draft': define({ id: 'status_layout.save_draft', label: 'Save layout draft', workspace: 'administration', permission: 'status_indicators.manage', allowedSurfaces: ['sticky_footer'], presentation: 'secondary', contextualLinks: false, allowMultiple: false }),
  'status_layout.publish': define({ id: 'status_layout.publish', label: 'Publish layout', workspace: 'administration', permission: 'status_indicators.manage', allowedSurfaces: ['sticky_footer', 'dialog'], presentation: 'primary', contextualLinks: false, allowMultiple: false }),
  'site.create': define({ id: 'site.create', label: 'Add site', workspace: 'administration', permission: 'sites.create', allowedSurfaces: ['workspace_header'], presentation: 'primary', contextualLinks: false, allowMultiple: false }),
  'site.view': define({ id: 'site.view', label: 'View details', workspace: 'administration', permission: 'sites.view', allowedSurfaces: ['resource_row'], presentation: 'secondary', contextualLinks: false, allowMultiple: true }),
  'site.edit': define({ id: 'site.edit', label: 'Edit site', workspace: 'administration', permission: 'sites.edit', allowedSurfaces: ['resource_detail', 'overflow_menu'], presentation: 'secondary', contextualLinks: false, allowMultiple: true }),
  'site.set_default': define({ id: 'site.set_default', label: 'Set as default', workspace: 'administration', permission: 'sites.set_default', allowedSurfaces: ['resource_detail', 'overflow_menu'], presentation: 'menu', contextualLinks: false, allowMultiple: true }),
  'site.disable': define({ id: 'site.disable', label: 'Disable site', workspace: 'administration', permission: 'sites.disable', allowedSurfaces: ['resource_detail', 'overflow_menu', 'dialog'], presentation: 'menu', contextualLinks: false, allowMultiple: true }),
  'site.enable': define({ id: 'site.enable', label: 'Enable site', workspace: 'administration', permission: 'sites.disable', allowedSurfaces: ['resource_detail', 'overflow_menu', 'dialog'], presentation: 'menu', contextualLinks: false, allowMultiple: true }),
  'site.remove': define({ id: 'site.remove', label: 'Remove site', workspace: 'administration', permission: 'sites.remove', allowedSurfaces: ['resource_detail', 'overflow_menu', 'dialog'], presentation: 'menu', contextualLinks: false, allowMultiple: true }),
  'site.restore': define({ id: 'site.restore', label: 'Restore site', workspace: 'administration', permission: 'sites.restore', allowedSurfaces: ['resource_detail', 'overflow_menu', 'dialog'], presentation: 'menu', contextualLinks: false, allowMultiple: true }),
  'site.transfer_resources': define({ id: 'site.transfer_resources', label: 'Resolve dependencies', workspace: 'administration', permission: 'sites.transfer_resources', allowedSurfaces: ['dialog'], presentation: 'secondary', contextualLinks: false, allowMultiple: true }),
  'site.view_audit': define({ id: 'site.view_audit', label: 'View audit history', workspace: 'administration', permission: 'sites.view_audit', allowedSurfaces: ['resource_detail', 'overflow_menu'], presentation: 'menu', contextualLinks: false, allowMultiple: true }),
}

interface ActionScopeValue {
  claims: Map<string, symbol>
}

const ActionScopeContext = createContext<ActionScopeValue | null>(null)

export interface DuplicateActionViolation {
  actionId: string
  count: number
}

export function findDuplicateActions(root: ParentNode = document): DuplicateActionViolation[] {
  const counts = new Map<string, number>()
  root.querySelectorAll<HTMLElement>('[data-action-id]').forEach((element) => {
    if (element.hidden || element.dataset.actionSuppressed === 'true') return
    const id = element.dataset.actionId
    if (!id) return
    const definition = ACTION_REGISTRY[id as CanonicalActionId]
    const resource = element.dataset.actionResource
    const identity = definition.allowMultiple && resource ? `${id}:${resource}` : id
    counts.set(identity, (counts.get(identity) ?? 0) + 1)
  })
  return [...counts]
    .filter(([, count]) => count > 1)
    .map(([identity, count]) => ({ actionId: identity.split(':')[0] ?? identity, count }))
}

function suppressUnexpectedDuplicates(root: ParentNode) {
  const byIdentity = new Map<string, HTMLElement[]>()
  root.querySelectorAll<HTMLElement>('[data-action-id]').forEach((element) => {
    const id = element.dataset.actionId
    if (!id) return
    const definition = ACTION_REGISTRY[id as CanonicalActionId]
    if (definition.allowMultiple && element.dataset.actionResource) return
    byIdentity.set(id, [...(byIdentity.get(id) ?? []), element])
  })
  for (const [id, elements] of byIdentity) {
    for (const duplicate of elements.slice(1)) {
      duplicate.hidden = true
      duplicate.dataset.actionSuppressed = 'true'
      console.error(`Suppressed duplicate canonical action: ${id}`)
    }
  }
}

export function ActionScope({ scopeKey, children }: { scopeKey: string; children: ReactNode }) {
  const value = useRef<ActionScopeValue>({ claims: new Map() }).current
  const root = useRef<HTMLDivElement>(null)
  useEffect(() => {
    value.claims.clear()
  }, [scopeKey, value])
  useEffect(() => {
    if (!root.current) return
    suppressUnexpectedDuplicates(root.current)
    const observer = new MutationObserver(() => {
      if (root.current) suppressUnexpectedDuplicates(root.current)
    })
    observer.observe(root.current, { childList: true, subtree: true })
    return () => { observer.disconnect() }
  })
  return (
    <ActionScopeContext.Provider value={value}>
      <div className="action-scope" ref={root}>{children}</div>
    </ActionScopeContext.Provider>
  )
}

export function CanonicalAction({
  id,
  surface,
  resourceKey,
  permitted = true,
  children,
}: {
  id: CanonicalActionId
  surface: ActionSurface
  resourceKey?: string
  permitted?: boolean
  children: ReactElement
}) {
  const scope = useContext(ActionScopeContext)
  const token = useRef(Symbol(id))
  const definition = ACTION_REGISTRY[id]
  const validSurface = definition.allowedSurfaces.includes(surface)
  const claimKey = definition.allowMultiple && resourceKey ? `${id}:${resourceKey}` : id
  const existing = scope?.claims.get(claimKey)
  const claimed = permitted && validSurface && (!existing || existing === token.current)
  if (claimed && scope && !existing) scope.claims.set(claimKey, token.current)
  useEffect(() => () => {
    if (scope?.claims.get(claimKey) === token.current) scope.claims.delete(claimKey)
  }, [claimKey, scope])
  if (!claimed || !isValidElement(children)) return null
  return cloneElement(children as ReactElement<Record<string, unknown>>, {
    'data-action-id': id,
    'data-action-label': definition.label,
    'data-action-surface': surface,
    'data-action-resource': resourceKey,
    'data-action-audit': definition.auditIdentity,
  })
}
