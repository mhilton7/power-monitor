# Legacy frontend production removal report

## Cutover result

The legacy source tree was removed after the rollback window closed. Its
history remains recoverable through Git, while the working tree now has one
canonical browser application at `frontend/`.

`deploy/docker/frontend.Dockerfile` installs and builds only
`frontend/`. The production build verifier rejects legacy page and route
module names, and applies the JavaScript bundle budget before the image can be
published.

## Legacy modules removed from production

- every retired workspace page module;
- the legacy `Layout`, sidebar, workspace, status-card, and administration
  shells;
- legacy page-specific CSS and state containers;
- legacy route registrations and navigation registries;
- alternate Devices, Topology, Enrollment, Usage, Costs, Rates, Rate Sources,
  Alerts, Administration, Users & Access, System Health, and Layout pages.

These files are absent from the working tree and therefore cannot be emitted as
production chunks.

## Retained implementation

No legacy React or CSS module is imported into `frontend`. The new
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
Set-Location frontend
npm run build
npm run architecture
```

The verifier fails if the generated `dist/` contains a legacy module marker or
exceeds the production JavaScript budget. The Dockerfile also runs the same
build before copying static assets to the final unprivileged Nginx image.

## Source consolidation

The rollback window is closed and physical source deletion is complete.
Rollback continues to select a previous immutable frontend image; it never
depends on rebuilding the retired comparison source.
