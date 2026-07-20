# TrueNAS dataset layout

Replace `POOL` everywhere with one existing TrueNAS pool name. `POOL` is a
fail-closed placeholder, not a valid deployment value. The application name and
the remainder of every path are fixed.

Create a parent dataset and seven child datasets in **Datasets > Add Dataset**:

| Dataset | Absolute host path | Container use | Snapshot policy |
|---|---|---|---|
| `apps/power-monitor/postgres` | `/mnt/POOL/apps/power-monitor/postgres` | PostgreSQL data | frequent, application-consistent snapshot before upgrades |
| `apps/power-monitor/backups` | `/mnt/POOL/apps/power-monitor/backups` | encrypted logical backups and checksums | daily; replicate off-system |
| `apps/power-monitor/firmware` | `/mnt/POOL/apps/power-monitor/firmware` | uploaded firmware artifacts | daily or on change |
| `apps/power-monitor/config` | `/mnt/POOL/apps/power-monitor/config` | Caddyfile and generated reports | daily or on change |
| `apps/power-monitor/secrets` | `/mnt/POOL/apps/power-monitor/secrets` | file-backed secrets and optional TLS key | snapshot only to encrypted, access-controlled targets |
| `apps/power-monitor/caddy-data` | `/mnt/POOL/apps/power-monitor/caddy-data` | Caddy CA, certificates, and ACME state | daily or on change |
| `apps/power-monitor/caddy-config` | `/mnt/POOL/apps/power-monitor/caddy-config` | Caddy runtime config state | daily or on change |

Create `config/reports` as a directory inside the `config` dataset. Do not turn
the application directories into SMB shares except temporarily for a protected
administrator transfer. Never expose `postgres`, `secrets`, or `caddy-data` over
an untrusted share.

Use the Generic dataset preset and apply the explicit ACLs from
[permissions.md](permissions.md). The Apps preset's conventional Apps group does
not replace the numeric container identities used by this stack.

Before installation, validate the final rendered YAML from an administrator
workstation:

```text
python tools/validate-truenas-compose.py --deployment --pool MYPOOL --gateway-port 8443 rendered-compose.yaml
```

The command fails if any `POOL` or all-zero digest placeholder remains, if paths
span pools, or if a required dataset root is absent.
