# Dashboard & Login Text

Open **Administration > Dashboard & Login Text**. The page edits only server-registered presentation keys. It cannot change routes, API paths, permission codes, protocol identifiers, database IDs, security errors, MFA wording, password policy, CSRF behavior, or device secrets.

## Workflow

1. Choose General, Login Screen, Navigation, Page Titles & Subtitles, or Footer & Support.
2. Edit approved fields and optionally record a reason.
3. **Save draft**. Current users remain on the published revision.
4. **Preview** the server-validated draft in desktop login, mobile login, and dashboard-shell views.
5. Review the base revision, draft revision, and changed-key count; then **Publish**.

Publishing validates the complete catalog, checks optimistic revisions, creates an immutable revision, audits it, and invalidates frontend caches. If another administrator published first, reload and rebase the draft.

**Reset field to default** and **Reset section to defaults** each publish a new revision using compiled defaults. **Revision History > Restore** copies an earlier revision into a new revision; history is never mutated. Drafts and published overrides may be exported as schema-versioned JSON. Import accepts registered keys into a draft only and still requires preview/publish.

## Catalog and safety

The code-backed catalog covers application names/tagline/title prefix, login heading/subtitle/field/button/help/support/footer, current navigation labels, page titles/subtitles, and dashboard footer/support/banner. Each definition declares visibility, type, required/blank behavior, length, line-break policy, URL companion, Markdown policy, and preview location.

Fields are plain text. The server rejects HTML-like markup, scripts, template expressions, control characters, oversized values, unknown keys, URL credentials, and schemes other than `https:` or `mailto:`. React renders values as escaped text. Arbitrary CSS, HTML, JavaScript, and executable templates are unsupported.

## Login fallback

`GET /api/v1/public/interface-text` returns only the application display name and approved login heading, subtitle, field labels, sign-in label, help/support, safe support URL, and footer, plus a revision and ETag. It never returns the organization tagline, application short name, drafts, users, editors, audit data, topology, or private settings. The login bundle contains safe compiled defaults. A timeout, unavailable endpoint, malformed payload, or missing key cannot hide the email/password/sign-in controls; the page continues with defaults. Security-critical authentication errors remain server-controlled.
