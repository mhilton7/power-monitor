# Rate sources

The bundled seed is `shared/schemas/sce-rates-2026-06-01.json`. Its supplied metadata says checked **2026-07-20** and effective **2026-06-01**. During this build the official sources were independently retrieved on **2026-07-19 America/Los_Angeles**; the date difference is retained explicitly rather than rewriting supplied metadata.

## Official sources checked

- [SCE residential TOU plans](https://www.sce.com/save-money/rates-financing/residential-rate-plans/time-of-use-plans): TOU-D-4-9PM, TOU-D-5-8PM, and TOU-D-PRIME period prices matched the supplied seed, including `$0.79/day` and the `$0.10/kWh` baseline credit where applicable.
- [SCE rate advisory](https://www.sce.com/save-money/rates-financing/sce-rate-advisory): stated that rates changed June 1, 2026, consistent with the intended effective date.
- [SCE Base Services Charge](https://www.sce.com/save-money/rates-financing/residential-rate-plans/bsc): states `$24.15/month`, described as approximately `$0.80/day`. This differs by one cent from the TOU page's displayed `$0.79/day`. The seed keeps `$0.79/day`, cites the TOU display, marks user verification required, and exposes the value for cloning into a corrected effective version.

Public TOU values assume SCE delivery and SCE generation. CCA and Direct Access generation are modeled separately. The service never scrapes SCE at runtime. Used rate versions are immutable; later corrections create new effective versions. No holiday schedule is assumed without a versioned source.

These presets are estimates and are not tariff sheets or utility bills. Operators must verify their plan code, eligibility, baseline allocation, generation provider, taxes, and bill adjustments.
