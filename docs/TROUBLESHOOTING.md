# Troubleshooting

- **Offline:** compare last signed heartbeat, API result, meter/SD/time evidence, and backlog. Ping alone is not proof.
- **IP changed:** inspect address history and DHCP; identity remains the device UUID. Validate the new address against site CIDRs.
- **Heartbeat works, pull fails:** verify push/pull mode, worker VLAN/VPN route, allowed port/CIDR/domain, TLS name, and device-local HMAC clock.
- **Signature or time error:** verify exact body bytes, canonical duplicate query sorting, directional HKDF info, UTC Unix time, nonce length/uniqueness, protocol string, and credential state. `/api/v1/time` is a hint, not trusted time.
- **Missing sequence:** inspect retained bounds. Retry pull/backfill; late records fill gaps. `410 Gone` means permanent device-side loss and must remain disclosed.
- **SD fault:** stop treating history as durable, repair/replace media on the sensor, preserve event evidence, and do not fabricate lost energy.
- **Incorrect aggregate:** inspect parent/child and split-phase roles. Never sum a parent with its children; a single service leg is not whole-home.
- **Unexpected cost:** confirm timezone, interval coverage, effective plan/version, cost scope, billing dates, baseline, fixed-charge count, CCA/DA, taxes, credits, and source date.
- **CCA mismatch:** public SCE prices include SCE generation; configure replacement/adjustment once.
- **DST/report difference:** compare UTC instants and offsets. Spring has no fabricated hour; fall repeats local labels with different offsets.
- **Worker unhealthy:** inspect JSON logs, PostgreSQL readiness, `worker_state`, polling timeouts, report/backup volumes, and advisory-lock ownership.
- **Migration failure:** keep services stopped, preserve logs, restore the latest verified backup if needed, and never mark readiness manually.
- **No downloadable application logs:** generate normal API or worker activity,
  confirm the chosen dates intersect the available range, and verify that the
  API, worker, and backup runtime UIDs can write the persistent logs mount. On
  TrueNAS, recheck the dedicated dataset ACL rather than broadening container
  privileges.
- **Log export fails or is too large:** narrow the date range or service filter,
  verify free temporary space, and inspect the audited failure category. The
  server intentionally refuses ranges outside the retained 90-day window and
  archives above its configured size cap.
- **Removed sensor still sends data:** confirm it is Archived and all credentials
  show revoked. An old signature must be rejected. Do not restore the credential;
  re-enroll the physical hardware with a new one-time token so it receives a new
  secret.
- **Cannot remove a sensor:** the action is administrator-only and the
  confirmation must exactly match the displayed friendly name or immutable UUID.
  A concurrent or already completed removal is safe to retry.
- **SCE check remains queued:** confirm the worker is healthy and owns the
  PostgreSQL advisory lock, then inspect the job and `rate_source_checks` rows.
  Retrying is idempotent; unchanged bytes do not create duplicate versions.
- **SCE source requires review:** download the archived hash-verified artifact.
  Unstructured or image-only documents intentionally block parsing; upload an
  official structured artifact or enter a reviewed custom version rather than
  enabling OCR or guessing.
- **Automatic activation was blocked:** review the candidate's explicit reasons,
  including parser warnings, missing dates, source conflicts, rate-change
  threshold, retroactivity, or provider-assumption changes. Keep the current
  verified version active and use manual approval only after verifying evidence.
