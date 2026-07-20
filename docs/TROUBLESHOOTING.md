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
