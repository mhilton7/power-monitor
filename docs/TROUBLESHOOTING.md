# Troubleshooting

## Browser password manager does not autofill sign-in

Use the exact stable HTTPS origin where the credential was saved. Changes from a
hostname to an IP address, HTTPS to HTTP, one port to another, or production to a
development URL create a different browser credential context. Confirm password
saving/autofill is enabled and the site is not on the never-save list. Repair
only the affected entry in the browser password manager and repeat the synthetic
account procedure in [Browser compatibility](BROWSER_COMPATIBILITY.md). Never
disable TLS verification or store the password in Power Monitor settings.

- **Offline:** compare last signed heartbeat, API result, meter/SD/time evidence, and backlog. Ping alone is not proof.
- **IP changed:** inspect address history and DHCP; identity remains the device UUID. Validate the new address against site CIDRs.
- **Heartbeat works, pull fails:** verify push/pull mode, worker VLAN/VPN route, allowed port/CIDR/domain, TLS name, and device-local HMAC clock.
- **Network page says deny all:** this is explicit. The old empty pull-CIDR list
  already denied every pull target and migration preserved that result. Add a
  private CIDR, then select **Allow listed private networks only**, or select
  **Allow all private networks** after review. Do not weaken TLS or signatures.
- **CIDR cannot be saved:** use a canonical RFC1918 IPv4 or IPv6 ULA network.
  Public, loopback, link-local, multicast, unspecified, metadata, malformed, and
  duplicate ranges are rejected. Overlaps produce a visible warning. The last
  enabled listed-mode CIDR cannot be disabled or removed until the mode changes.
- **Add current private network is unavailable:** the server saw a loopback,
  public, link-local, or untrusted-proxy address and intentionally made no guess.
  Enter the narrow sensor VLAN manually; do not substitute a Docker proxy range.
- **Signature or time error:** verify exact body bytes, canonical duplicate query sorting, directional HKDF info, UTC Unix time, nonce length/uniqueness, protocol string, and credential state. `/api/v1/time` is a hint, not trusted time.
- **Missing sequence:** inspect retained bounds. Retry pull/backfill; late records fill gaps. `410 Gone` means permanent device-side loss and must remain disclosed.
- **SD fault:** stop treating history as durable, repair/replace media on the sensor, preserve event evidence, and do not fabricate lost energy.
- **Incorrect aggregate:** inspect parent/child and split-phase roles. Never sum a parent with its children; a single service leg is not whole-home.
- **Unexpected cost:** confirm timezone, interval coverage, effective plan/version, cost scope, billing dates, baseline, fixed-charge count, CCA/DA, taxes, credits, and source date.
- **Current rate says unavailable:** open **Administration > Sites & accounts**.
  Confirm the account is active, a published version covers the current instant,
  the site timezone is correct, and no overlapping window was rejected. A rate
  can be ready before a sensor; live power cannot.
- **Cost is unavailable although the rate is ready:** assign the relevant sensor
  or aggregate to the account and wait for durable readings. Energy-only cost is
  not `$0.00` when readings are missing. Full-account charges additionally need
  an explicit complete-account aggregate or an audited override.
- **History cost is unavailable:** confirm every selected sensor is assigned to
  a utility account with a rate assignment covering the historical interval.
  Electrical data remains valid; do not interpret unavailable cost as zero.
- **History combined selection is rejected:** inspect the named sensors and
  circuit tree for a parent/child or duplicate-circuit overlap. Correct topology,
  choose only non-overlapping meters, or use Individual sensors for comparison.
- **History total looks low:** inspect bucket coverage, missing sensor IDs, and
  quality flags. Partial totals include only valid contributors and never assume
  a missing sensor consumed zero. Enable strict coverage to withhold them.
- **History chart is too large:** choose Automatic or a coarser bucket and a
  shorter range. Raw history is limited to two days; all History queries are
  bounded to protect ingestion and PostgreSQL.
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
- **An indicator disappeared:** check the active page, role, breakpoint, site,
  and the user's underlying data permission in **Status Indicators & Layout**.
  Disabled items appear in the tray. Monitoring and alert rules continue even
  when their summary indicator is hidden.
- **A blank status row remains:** hard-refresh the published revision and inspect
  the resolved `/api/v1/status-indicators/layout` response. A valid empty zone
  has no `data-status-zone` wrapper; an old frontend image may still contain a
  fixed page summary and should be upgraded by immutable digest.
- **Publish reports a conflict:** another administrator published after this
  draft's base revision. Export the draft if needed, reload the current revision,
  reapply the intended changes, preview, and publish. Do not bypass the stale
  revision check.
- **A saved layout references a retired indicator:** the server ignores the
  retired key and reports a warning so existing pages remain usable. Remove the
  old override in a new draft; imports reject unknown keys.
