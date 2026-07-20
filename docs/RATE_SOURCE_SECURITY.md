# Rate source security

Automated retrieval is limited to the four seeded `https://sce.com` or
`https://www.sce.com` URLs and approved tariff-PDF path prefixes. The client
rejects credentials, non-443 ports, private/reserved DNS answers, unsupported
paths, unsafe redirects, unsupported content types, oversized responses, and
unbounded retry behavior. TLS verification is always enabled.

Responses are stored outside the database in the rate-source artifact dataset.
Database evidence records contain the SHA-256, byte length, media type,
retrieval metadata, parser identity/version, warnings, errors, and links to the
candidate/version. Downloads re-resolve the configured archive root and verify
the hash before serving the file.

Do not add arbitrary source URLs, OCR, browser-side retrieval, cookies, or
authorization headers to adapters. An unstructured or image-only official
document is archived for manual review and never guessed.
