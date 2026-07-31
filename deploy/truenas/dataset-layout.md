# TrueNAS dataset layout

This deployment uses the existing TrueNAS pool named `Apps`. Application bind
mounts are rooted at `/mnt/Apps/Power/power-monitor/`, while PostgreSQL uses the
dedicated existing dataset `/mnt/Apps/Power/postgres`. The checked-in Compose
template keeps `POOL` as a fail-closed placeholder; render it with `--pool Apps`
instead of editing individual paths.

Create `Power/power-monitor`, its application child datasets, and the dedicated
`Power/postgres` sibling dataset in **Datasets > Add Dataset**:

| Dataset | Absolute host path | Container use | Snapshot policy |
|---|---|---|---|
| `Power/postgres` | `/mnt/Apps/Power/postgres` | PostgreSQL data | frequent, application-consistent snapshot before upgrades |
| `Power/power-monitor/backups` | `/mnt/Apps/Power/power-monitor/backups` | encrypted logical backups and checksums | daily; replicate off-system |
| `Power/power-monitor/firmware` | `/mnt/Apps/Power/power-monitor/firmware` | uploaded firmware artifacts | daily or on change |
| `Power/power-monitor/logs` | `/mnt/Apps/Power/power-monitor/logs` | daily structured application and backup logs | daily; retain at least the application-managed 90-day window |
| `Power/power-monitor/rate-source-artifacts` | `/mnt/Apps/Power/power-monitor/rate-source-artifacts` | immutable SCE evidence plus private utility-bill PDFs, sanitized evidence, hashes, and extraction metadata | daily and before upgrades; replicate with encrypted backups |
| `Power/power-monitor/config` | `/mnt/Apps/Power/power-monitor/config` | Caddyfile and generated reports | daily or on change |
| `Power/power-monitor/secrets` | `/mnt/Apps/Power/power-monitor/secrets` | file-backed secrets and optional TLS key | snapshot only to encrypted, access-controlled targets |
| `Power/power-monitor/caddy-data` | `/mnt/Apps/Power/power-monitor/caddy-data` | Caddy CA, certificates, and ACME state | daily or on change |
| `Power/power-monitor/caddy-config` | `/mnt/Apps/Power/power-monitor/caddy-config` | Caddy runtime config state | daily or on change |

Create `config/reports` as a directory inside the `config` dataset. Do not turn
the application directories into SMB shares except temporarily for a protected
administrator transfer. Never expose `postgres`, `secrets`, or `caddy-data` over
an untrusted share.

Use the Generic dataset preset and apply the explicit ACLs from
[permissions.md](permissions.md). The Apps preset's conventional Apps group does
not replace the numeric container identities used by this stack. The logs
dataset persists independently of container replacement and must be writable by
both UID 10001 and UID 10003.

Before installation, validate the final rendered YAML from an administrator
workstation:

```text
python tools/validate-truenas-compose.py --deployment --pool Apps --gateway-port 8443 rendered-compose.yaml
```

The command fails if any `POOL` or all-zero digest placeholder remains, if paths
span pools, or if a required dataset root is absent.

Utility-bill originals are stored below
`rate-source-artifacts/utility-bills/originals` and are not web assets. Do not
share this dataset through SMB/NFS. The API (UID 10001) needs Modify access for
upload and approved retention deletion; the backup identity (UID 10003) needs
Read access only.
