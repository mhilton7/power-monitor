# Weekly SCE rates and custom plans implementation record

This record maps `CODEX_PROMPT_WEEKLY_SCE_RATES_AND_CUSTOM_PLANS.txt` to the
production implementation. It is maintained with the code and is the release
checklist for this feature.

## Scope and invariants

- The existing device protocol remains `pm-protocol/1.0.0`.
- Rate money and energy values are represented with `Decimal`; timestamps are
  stored in UTC and tariff schedules evaluate in the utility-account timezone.
- Existing SCE seed versions and historical cost results remain reproducible.
- Active rate versions are immutable. Edits always create a new draft version.
- Fixed charges and baseline credits are applied only to an explicitly selected
  full-account estimate and never to the default one-CT energy-only scope.
- Public source content is evidence, not executable configuration. A candidate
  must pass validation and the configured approval policy before activation.

## Implementation map

| Requirement | Implementation |
| --- | --- |
| Weekly scheduling, lock, job history | `worker/app/rate_sync.py`, `background_jobs`, `rate_sync_configuration` |
| Approved-source SSRF controls | `backend/app/rates/sources.py` HTTPS SCE host/path allowlist plus audited managed-source API |
| Conditional retrieval and evidence | `rate_source_checks`, `rate_source_artifacts`, artifact filesystem |
| Parser registry and normalized schema | `backend/app/rates/sources.py`, `backend/app/rates/documents.py` |
| Candidate diff, review, approval | `rate_change_candidates`, `rate_candidate_differences`, `rate_approval_decisions` and admin API |
| Custom plan lifecycle | `/api/v1/rates/plans` draft/validate/activate/clone/import/export/assign endpoints |
| Cost integration | normalized rate documents persisted on the exact `rate_versions` used by cost runs |
| Dashboard workflows | Rates library, four-step editor, source settings, candidate review pages |
| TrueNAS persistence | `/mnt/Apps/Power/power-monitor/rate-source-artifacts` bind mount |

## Release verification

The release is accepted only after the repository build commands in
`AGENTS.md`, migration upgrade tests, source fixtures, frontend E2E tests,
Docker image builds, Compose validation, and backup/restore verification pass.
Network retrieval tests use local deterministic fixtures and never depend on
the live SCE site.
