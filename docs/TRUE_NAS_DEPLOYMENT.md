# TrueNAS deployment notes

The production TrueNAS Community Edition deployment is maintained under
`deploy/truenas/`. Replace `POOL` with the real pool name; for the documented
layout this renders paths rooted at `/mnt/Apps/Power/power-monitor/`, including
`rate-source-artifacts`.

Use **Apps > Discover Apps > Install via YAML** in the TrueNAS web interface.
Do not operate the production application with ad-hoc Docker commands. Follow
the exact dataset ACLs, file-backed secret generation, immutable image pins,
installation, upgrade/rollback, and backup/restore procedures in the TrueNAS
documents. The rate artifact dataset is mounted read-write only into API and
worker, read-only into backup, and is included in checksummed and optionally
encrypted logical backups.
