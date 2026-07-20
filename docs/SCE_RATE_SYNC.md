# SCE rate synchronization

The worker checks the four server-controlled SCE sources every Sunday at
03:15 `America/Los_Angeles`, with deterministic 0–20 minute jitter. The
default policy is `manual_review`: retrieval, hashing, archiving, parsing,
validation, and differences are automatic; activation is not.

Administrators and rate managers can select **Rates > Check SCE now**. The API
returns a job ID immediately and the page polls `/api/v1/jobs/{job_id}`. A
failed source, parser warning, missing effective date, conflict, or validation
error leaves the current verified version active.

Source priority is filed tariff or structured official data, the public SCE
TOU page, the SCE advisory as a change/effective-date signal, then an uploaded
official artifact. URLs cannot be added in the browser. Redirects remain
subject to the exact HTTPS host/path allowlist and public-DNS checks.

`notify_only` archives and compares changes without creating an activatable
candidate. `auto_activate_verified` is separately armed and is blocked unless
the archived official evidence, approved parser version, explicit date,
complete schedules, warning-free validation, change threshold, provider
assumption, and retroactivity checks all pass.

See [rate automation and custom plans](rate-automation-and-custom-plans.md) for
configuration and recovery details.
