# Rate sources

## Manual checks

**Sources > Check now** queues a real `rate_source_sync` background job and
returns its job identifier. Only one queued or running job exists for the same
source scope; repeated clicks join the existing run instead of duplicating
network retrieval. The browser polls job status and renders queued, running,
completed, partial, or failed state with per-source results, timestamps,
artifact counts, candidate counts, and safe error summaries.

Each enabled HTTPS source records its last check result. Completed runs remain
available from the source-check history endpoint, and a failed source can be
retried without rerunning unrelated sources. Retrieval, parsing, evidence
archival, candidate creation, retry, and completion are audited against the
same job. Private uploaded-bill evidence remains disabled as a network source
and is never fetched by the public source checker.

An uploaded utility bill is archived through the same artifact and extraction
evidence infrastructure as a managed rate source. Its default role is
supporting; an administrator may review it as authoritative account-specific or
reference-only evidence. Conflicts preserve both values and block automatic
activation. One bill never automatically replaces an approved utility source.
See [Utility-bill PDF imports](UTILITY_BILL_IMPORTS.md).

The bundled seed is `shared/schemas/sce-rates-2026-06-01.json`. Its supplied metadata says checked **2026-07-20** and effective **2026-06-01**. During this build the official sources were independently retrieved on **2026-07-19 America/Los_Angeles**; the date difference is retained explicitly rather than rewriting supplied metadata.

## Official sources checked

- [SCE residential TOU plans](https://www.sce.com/save-money/rates-financing/residential-rate-plans/time-of-use-plans): TOU-D-4-9PM, TOU-D-5-8PM, and TOU-D-PRIME period prices matched the supplied seed, including `$0.79/day` and the `$0.10/kWh` baseline credit where applicable.
- [SCE rate advisory](https://www.sce.com/save-money/rates-financing/sce-rate-advisory): stated that rates changed June 1, 2026, consistent with the intended effective date.
- [SCE Base Services Charge](https://www.sce.com/save-money/rates-financing/residential-rate-plans/bsc): states `$24.15/month`, described as approximately `$0.80/day`. This differs by one cent from the TOU page's displayed `$0.79/day`. The seed keeps `$0.79/day`, cites the TOU display, marks user verification required, and exposes the value for cloning into a corrected effective version.

Public TOU values assume SCE delivery and SCE generation. CCA and Direct Access generation are modeled separately. The scheduled worker retrieves and archives enabled approved SCE sources, and the public TOU adapter deterministically extracts published schedule blocks into review candidates. Used rate versions are immutable; later corrections create new effective versions. No holiday schedule is assumed without a versioned source.

Managed candidates may also contain flat, tiered, or hybrid plan documents.
Their evidence archive retains pricing model, tier order/IDs, boundaries,
baseline method and citation, hybrid combination method, and per-period prices.
A pricing-model change is always held for explicit administrator review; it is
never auto-activated solely because parsing succeeded. The photographed tier
values supplied for development exist only in the deterministic fixture
`shared/examples/tiered-rate-plan.json` and are not production SCE data.

These presets are estimates and are not tariff sheets or utility bills. Operators must verify their plan code, eligibility, baseline allocation, generation provider, taxes, and bill adjustments.
