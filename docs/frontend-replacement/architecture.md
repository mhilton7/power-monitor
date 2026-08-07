# Single Home frontend architecture

`frontend` is the production React and TypeScript application. It does not
import the legacy shell, routes, page components, feature components, or CSS.
The production image copies only this directory.

## Production destinations

| Destination | Homeowner purpose | Server authority |
|---|---|---|
| Home | Live load, today's energy and cost, current rate, sensors, cycle projection, actionable alerts | Fleet summary, signed device state, history query, utility-account tier status, alerts |
| History | Whole-home or individual-sensor power, energy, exact interval cost, coverage, provenance, CSV export | Server history aggregation and historically effective rate versions |
| Billing | Electric service, current plan, billing cycle, secure bill import, past bills, owner-only detailed rates | Utility accounts, rate versions and assignments, strict bill parser/OCR/evidence |
| Settings | Home, Sensors, Family Access, Notifications, Appearance, Data & Backups, Advanced | Existing permission-checked management APIs |

Alerts use one global drawer. No normal route renders a fifth workspace.

## State and transport

- `AuthProvider` owns the one session query.
- `SingleHomeProvider` resolves exactly one active home and stops cutover when
  more than one active home exists.
- `LiveHomeProvider` owns the shared sensor, electric-service, summary, alert,
  and billing-cycle queries. One event stream invalidates live queries; polling
  is only the fallback.
- `api/client.ts` is the only fetch boundary. It supplies same-origin
  credentials and CSRF and converts RFC 9457-style problems to `ApiError`.
- `api/adapters.ts` validates container shapes and translates internal server
  terms into homeowner models. Decimal money, energy, and rate values remain
  strings until the display formatter.
- The browser never receives device credentials, signing keys, API addresses,
  enrollment secrets after creation, database credentials, backup paths, or
  cryptographic keys.

## Single Home migration behavior

Zero active homes opens the persisted nine-step onboarding flow. One active
home becomes the Single Home identity. More than one active home produces a
blocking migration message rather than guessing or rewriting data. Disabled
and removed homes remain retained server records.

The setup flow creates real server resources at each durable step. Bill and
sensor steps can be skipped. The local step cursor only restores presentation
progress; it is never the authority for whether a home, service, bill, or
sensor exists.

## Design and accessibility

The design system is token-based and supports dark, light, and system themes,
comfortable and compact density, a configurable accent, desktop rail, tablet
layout, and mobile bottom navigation. Layout uses grid/flex flow rather than
fixed content coordinates. Every form uses native labels and controls, dialogs
have modal semantics, charts include accessible tables, status is never
communicated by color alone, and reduced-motion preferences are respected.

## Build boundaries

`scripts/verify-production-bundle.mjs` rejects legacy page tokens and enforces a
1.25 MB uncompressed JavaScript budget. The current bundle is split into React,
query, chart, icon, and application chunks. `deploy/docker/frontend.Dockerfile`
sets `VITE_SINGLE_HOME_MODE=true` and copies only `frontend`.
