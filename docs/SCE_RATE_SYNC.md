# SCE rate synchronization

The worker checks the enabled approved SCE sources every Sunday at
03:15 `America/Los_Angeles`, with deterministic 0–20 minute jitter. The
default policy is `manual_review`: retrieval, hashing, archiving, parsing,
validation, and differences are automatic; activation is not.

Administrators and rate managers can select **Rates > Check SCE now**. The API
returns a job ID immediately and the page polls `/api/v1/jobs/{job_id}`. A
failed source, parser warning, missing effective date, conflict, or validation
error leaves the current verified version active.

Source priority is filed tariff or structured official data, the public SCE
TOU page, the SCE advisory as a change/effective-date signal, then an uploaded
official artifact. Administrators and rate managers can add an approved SCE
rate page, index, advisory, or tariff PDF under **Rates > Rate source
settings**. The server validates the host, path, parser, and effective-date
requirements before storing it. Redirects remain subject to the same HTTPS
SCE path allowlist and public-DNS checks.

The public TOU adapter extracts SCE's plan headings, summer/winter boundaries,
weekday/weekend blocks, published prices, base service charge, and baseline
credit into normalized candidate documents. A supplied effective date is
mandatory, and the active rate is unchanged until the configured review
workflow approves and activates the candidate.

`notify_only` archives and compares changes without creating an activatable
candidate. `auto_activate_verified` is separately armed and is blocked unless
the archived official evidence, approved parser version, explicit date,
complete schedules, warning-free validation, change threshold, provider
assumption, and retroactivity checks all pass.

See [rate automation and custom plans](rate-automation-and-custom-plans.md) for
configuration and recovery details.
