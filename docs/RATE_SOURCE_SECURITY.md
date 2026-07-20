# Rate source security

Automated retrieval starts with four seeded `https://sce.com` or
`https://www.sce.com` sources. Administrators and rate managers may add sources
under the approved SCE rate and tariff path prefixes. Direct documents must be
PDF files under approved SCE document paths. The client rejects credentials,
non-443 ports, private/reserved DNS answers, traversal, unsupported paths,
unsafe redirects, unsupported content types, oversized responses, and
unbounded retry behavior. TLS verification is always enabled.

Adding a source requires a name, an approved URL, and a registered parser. SCE
TOU summary pages also require the effective date from a supporting advisory or
filed tariff; retrieval time is never treated as the tariff effective date.
Administrator-managed URLs can create manual-review candidates but are not in
the static evidence set permitted for strict automatic activation.

Responses are stored outside the database in the rate-source artifact dataset.
Database evidence records contain the SHA-256, byte length, media type,
retrieval metadata, parser identity/version, warnings, errors, and links to the
candidate/version. Downloads re-resolve the configured archive root and verify
the hash before serving the file.

Do not expand the managed prefixes to arbitrary hosts, or add OCR, browser-side
retrieval, cookies, or authorization headers to adapters. An unstructured or
image-only official document is archived for manual review and never guessed.
