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

Use Chrome DevTools responsive mode for 3440×1440, 2560×1440, 1920×1080,
1440×900, 1024×768, 768×1024, and 390×844. At each size, repeat at Chrome zoom
80%, 100%, 125%, and 150%. Test both dark and light themes where available.

On Billing, History, and Settings > Electricity rate > Advanced Rate Settings,
confirm:

- no document-level horizontal scrollbar, clipped text, overlapping metadata,
  fixed-navigation collision, hidden action, or orphan gap;
- wide content remains centered and does not exceed 1,680 px;
- metric grids collapse cleanly, Billing actions remain reachable, and the
  billing-cycle empty state stays compact;
- only designated tab, segmented-control, table, and version/evidence regions
  scroll internally when their content requires it;
- Advanced Rate Settings tabs respond to Tab, Arrow Left/Right, Home, and End
  and announce the selected tab and panel correctly; and
- the Advanced System Health disclosure shows the expected frontend version,
  commit, and active hashed CSS asset.

Navigate every action by keyboard and verify visible focus, labels, dialog
focus, reduced motion, chart table access, and status text independent of
color.
