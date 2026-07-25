# Manual frontend verification

Run this checklist in current Chrome after the automated gates.

## Sign in and onboarding

1. Confirm email and current-password autofill appears and submits.
2. Confirm MFA appears only when required.
3. With a clean database, complete the owner bootstrap and verify the nine setup
   steps resume after refresh.
4. Skip bill and sensor, finish, then add both from their canonical locations.

## Four destinations

Confirm the desktop rail and mobile bottom navigation contain exactly Home,
History, Billing, and Settings. The alert bell opens a drawer. Check `/overview`,
`/devices`, `/topology`, `/rates`, `/bill-import`, `/alerts`, and
`/administration` bookmarks redirect without flashing a legacy page.

## Data paths

- Send a signed heartbeat and reading; verify Home live load and sensor health.
- Backfill readings; verify History coverage, gaps, whole-home aggregation,
  individual sensor mode, exact cost tooltip, provenance, and export.
- Assign flat, TOU, tiered, and hybrid test plans; verify current price/period
  and billing-cycle tier progress.
- Import text-layer and scanned SCE fixtures; verify review, evidence, separate
  plan/cycle outputs, and no automatic activation.
- Invite, disable, enable, remove, and restore a non-owner test user. Verify
  last-owner and self safeguards.
- Configure and test SMTP without observing a password in responses or logs.
- Request a backup and a restore preflight. Confirm only the isolated backup
  service handles filesystem and database operations.

## Responsive and accessible behavior

At 1440×1000, 834×1194, and 412×915 in dark and light themes, check for clipped
text, horizontal page scrolling, fixed-nav overlap, hidden actions, or orphan
gaps. Navigate every action by keyboard, verify visible focus, labels, dialog
focus, reduced motion, high zoom, chart table access, and status text independent
of color.
