# Legacy frontend production removal report

## Cutover result

The legacy `frontend/` source tree remains in the repository only as a
regression comparison target while the replacement is stabilized. It is not
copied into, imported by, or served from the production frontend image.

`deploy/docker/frontend.Dockerfile` installs and builds only
`frontend-next/`. The production build verifier rejects legacy page and route
module names, and applies the JavaScript bundle budget before the image can be
published.

## Legacy modules removed from production

- every module below `frontend/src/pages/`;
- the legacy `Layout`, sidebar, workspace, status-card, and administration
  shells;
- legacy page-specific CSS and state containers;
- legacy route registrations and navigation registries;
- alternate Devices, Topology, Enrollment, Usage, Costs, Rates, Rate Sources,
  Alerts, Administration, Users & Access, System Health, and Layout pages.

These files are absent from the container build context stage and therefore
cannot be emitted as production chunks.

## Retained implementation

No legacy React or CSS module is imported into `frontend-next`. The new
application independently uses React, a small same-origin History API router,
TanStack Query, Chart.js, Lucide, TypeScript, and Vite from its own lockfile. Existing concepts were
reimplemented through the server contract rather than copied from legacy page
components.

## Compatibility redirects

Legacy browser routes redirect inside the new router:

- `/overview`, `/dashboard`, and `/monitoring` to `/home`;
- `/usage` and `/analytics` to `/history`;
- `/rates`, `/rate-sources`, `/bill-import`, and `/costs` to `/billing`;
- `/devices`, `/enrollment`, and `/topology` to focused Sensor settings;
- `/alerts` to Home with the alert drawer requested;
- `/administration`, `/users-access`, and `/status-indicators` to focused
  Settings sections.

No redirect mounts or dynamically imports a legacy component.

## Bundle evidence

Run:

```powershell
Set-Location frontend-next
npm run build
npm run architecture
```

The verifier fails if the generated `dist/` contains a legacy module marker or
exceeds the production JavaScript budget. The Dockerfile also runs the same
build before copying static assets to the final unprivileged Nginx image.

## Deferred source deletion

Physical deletion of `frontend/` is intentionally deferred for one rollback
window. Rollback selects the previous immutable frontend image; it does not
rebuild legacy source. After the rollback window closes, the comparison source
may be deleted in a separate cleanup commit without affecting production.
