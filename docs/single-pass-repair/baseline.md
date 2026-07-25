# Single-pass repair baseline

## Supplied production evidence

- `reference-pdf-import-blank-overlay.png`: Billing is blurred by a full-screen
  overlay while no dialog is visible.
- `reference-advanced-rate-sources-current.png`: Advanced Rate Settings renders
  correctly as a disclosure and tab set, but the source list exposes technical
  URLs/parser labels and the custom editor remains feature-incomplete.
- `reference-home-dashboard-formatting-broken.png`: Home uses the greenfield
  shell but renders a wide, sparse no-sensor panel with no compact summary or
  supporting setup structure.

## Baseline acceptance status

| Gate | Status | Evidence |
| --- | --- | --- |
| Import dialog visible above backdrop | FAIL | Fixed backdrop paints above unpositioned workflow |
| Import focus/Escape/return/scroll lifecycle | FAIL | No modal lifecycle implementation |
| Flat, tiered, TOU, and hybrid editor | PARTIAL | Minimal model/rate/tier fields only |
| Advanced lifecycle actions | FAIL | Full validate/publish/assign/retire/remove flow absent |
| Typed versions/evidence/source presentation | PARTIAL | Plan-derived placeholder rows |
| Home no-sensor information architecture | FAIL | Heading plus oversized generic empty surface |
| Home configured information architecture | PARTIAL | Values exist; shared layout contracts do not |
| Responsive and visual matrix | FAIL | Required states are not covered |
| Backend/PDF/rate-engine/firmware preservation | PASS | No compatibility change is needed |
