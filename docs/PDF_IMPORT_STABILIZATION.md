# PDF import context stabilization

## Root cause

The production importer requested the legacy `GET /api/v1/utility-accounts`
response and treated every item as the richer management-account shape used by
another page. The legacy serializer intentionally returned flat compatibility
fields and did not return a `rate_context` object. `BillImportWorkspace` then
evaluated:

```text
selectedAccount.rate_context.current_plan
```

for a defined account whose `rate_context` parent was `undefined`. The
source-mapped development reproduction failed in `BillImportPage.tsx` at
line 560, column 81 with:

```text
TypeError: Cannot read properties of undefined (reading 'current_plan')
```

The React component stack identified `BillImportWorkspace`. This was a
backend/frontend response-shape mismatch, not a missing sensor and not an
account-without-a-plan condition. The old browser test supplied a richer mock
than the real endpoint and therefore did not detect the mismatch.

## Stabilized contract

The importer now loads one purpose-built
`utility-account-rate-context/1.0` contract from
`GET /api/v1/admin/utility-bill-import-context`. It always contains:

- explicit nullable account, plan, assignment, rate-version, and current-period
  values;
- a site-scoped list of accounts the caller may use;
- separate account-configured, rate-assigned, and rate-effective readiness;
- backend, API, and generated-client schema versions.

The Pydantic response model, generated OpenAPI, JSON Schema, example, generated
TypeScript model, and runtime parser are contract-tested together. The browser
rejects an incomplete or incompatible response as a structured application
error before it enters component state.

The legacy account endpoint remains compatible for existing clients. It is no
longer an importer dependency.

## Importer state and modes

The importer owns a discriminated state rather than inferring readiness from
overlapping booleans. Initializing, upload-ready, uploading, extracting,
review, applying, complete, recoverable-error, and fatal-error states all have
visible output.

Initialization distinguishes:

- a new Custom Plan;
- an existing Custom Plan draft;
- a draft cloned from a published version;
- an account with no plan;
- an account with a plan;
- no selected account; and
- a legacy-route redirect.

An existing plan is comparison context only. A missing account or plan never
blocks tariff extraction. An unassigned import creates private, user-scoped
evidence and separate plan/cycle drafts; account assignment and billing-cycle
application remain deferred. Publication, assignment, and cycle application
stay separate administrator actions.

## Query, route, and Retry behavior

Account IDs, bill IDs, and revisions are included in query keys. Queries that
require an ID remain disabled until it is present, requests use abort signals,
and late account responses cannot replace the editor document. A direct
`bill_id` refresh opens review immediately and reconstructs account context from
the server. The legacy route preserves safe query parameters and redirects into
the canonical Custom Plan editor.

Context Retry is bounded to three explicit attempts. It refetches only the
failed context, preserves the Custom Plan draft and selected artifact, and does
not resubmit a PDF. Uploads carry a client idempotency key and remain
content-hash idempotent on the server. A safe **Continue without account**
action is available for transient context failures, but not for malformed or
incompatible contracts.

## Error containment

Structured error boundaries protect the application shell, authenticated
workspace, Billing route, Custom Plan editor, importer, and evidence viewer.
Each fallback has a correlation ID and recovery action. Administrator-only
technical details may include a stack and safe route/version context; PDF
content, account identifiers, credentials, tokens, and signatures are never
logged. Ordinary UI never prints a raw JavaScript exception.

## Release compatibility

The frontend embeds its release version, commit, API schema, and generated
bill-import schema. The backend reports its release version, commit, API
schema, importer schema, and protocol version. Authenticated bootstrap blocks
the workspace with a safe diagnostic when API or importer schema versions do
not match.

API, frontend, backup, worker, migration, and gateway images must be rendered
from one immutable release. A mixed TrueNAS release is unsupported even when
individual containers are healthy.

## Security preserved

The correction does not change administrator authorization, CSRF, local-only
processing, PDF magic/MIME checks, byte/page limits, encrypted/active-content
rejection, bounded OCR fallback, content hashing, private evidence access,
confidence/review evidence, exact Decimal values, UTF-8 formatting, or the
no-auto-publish/no-auto-assignment rules.

