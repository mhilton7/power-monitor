# Canonical action registry

User-visible actions are identified by stable names in `frontend/src/actions.tsx`. Each registry record defines its owner workspace, permission, allowed presentation surfaces, primary/secondary/menu treatment, route where applicable, repeatability, and audit identity.

The rendered attributes are:

- `data-action-id`: stable action identity;
- `data-action-label`: canonical human label;
- `data-action-surface`: approved UI surface;
- `data-action-resource`: resource UUID for repeatable row/detail actions;
- `data-action-audit`: server-side audit identity.

## Duplicate prevention

An action scope claims each non-repeatable identity once. A second claim is not rendered. A DOM observer is a defensive backstop for asynchronously mounted content and suppresses an unexpected duplicate while reporting it to the console. Resource actions may repeat only when each instance has a different resource UUID.

`findDuplicateActions()` provides the test contract. Unit and Playwright tests fail if the same non-repeatable action, or the same repeatable action for one resource, appears more than once.

## Important identities

| Area | Canonical actions |
| --- | --- |
| Utility accounts | `utility_account.create`, `utility_account.manage`, `utility_account.recalculate`, `billing_statement.import` |
| Rates | `rate_plan.create_custom`, `rate_plan.import_from_bill`, `rate_plan.clone`, `rate_source.create`, `rate_source.check` |
| Monitoring | `device.enroll` |
| Access | `user.add`, `user.disable`, `user.remove` |
| Operations | `backup.create`, `logs.export` |
| Interface | `interface_text.save_draft`, `interface_text.publish`, `status_layout.save_draft`, `status_layout.publish` |
| Sites | `site.create`, `site.view`, `site.edit`, `site.set_default`, `site.disable`, `site.enable`, `site.remove`, `site.restore`, `site.transfer_resources`, `site.view_audit` |

## Billing decision

The UI deliberately distinguishes:

- **Import rate plan from bill** (`rate_plan.import_from_bill`) in the Custom Plan editor. It creates reviewable rate-plan and cycle drafts and never auto-activates a plan.
- **Import billing statement** (`billing_statement.import`) in utility-account detail. It is account-scoped usage/billing evidence.

There is no Bill Import workspace tab, generic upload side panel, or duplicate Utility Accounts upload button.

When adding an action, register it first, choose one owner surface, add the correct server permission and audit action, wrap the control in `CanonicalAction`, and extend unit/E2E duplicate coverage.

