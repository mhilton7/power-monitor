# Dataset permissions and container identities

The production images run with these exact numeric identities. These values are
also enforced by `tools/validate-truenas-compose.py`.

| Service | UID | GID |
|---|---:|---:|
| API, migration, worker | 10001 | 10001 |
| Caddy gateway | 10002 | 10002 |
| backup scheduler/restore tool | 10003 | 10003 (supplementary shared-log GID 10001) |
| frontend (`nginx-unprivileged`) | 101 | 101 |
| PostgreSQL (`bookworm` image) | 999 | 999 |

## Configure ACLs in the TrueNAS web interface

Perform these steps before installing the App, while every dataset is empty.
All datasets in the matrix below are children of
`/mnt/Apps/Power/power-monitor/`.

1. Open **Datasets**, select the child dataset, and choose **Permissions > Edit ACL**.
2. Use an NFSv4 ACL. Remove broad `Everyone@` write access. Keep the mandatory
   owner/group entries, but do not grant anonymous write access.
3. For each numeric identity in the table below choose **Add Item**, set **Who**
   to **User**, enter the numeric UID (not a similarly named local account), set
   the permission preset shown, and enable inheritance for files and directories.
4. Set **Apply permissions recursively** only during this initial setup or after
   separately protecting an existing dataset. Save the ACL.
5. Re-open the ACL and confirm the numeric IDs are unchanged. A TrueNAS user with
   the same display name is not a substitute for the number.

Exact ACL matrix:

| Dataset | Numeric user ACEs | Required access |
|---|---|---|
| `postgres` | UID 999 | Full Control / Modify, traverse, inherit |
| `backups` | UID 10003; UID 10001 | 10003 Modify; 10001 Read, traverse |
| `firmware` | UID 10001; UID 10003 | 10001 Modify; 10003 Read, traverse |
| `logs` | UID 10001; UID 10003 | Both UIDs Modify, traverse, and inherit; no access for frontend, gateway, or PostgreSQL |
| `rate-source-artifacts` | UID 10001; UID 10003 | 10001 Modify, traverse, inherit; 10003 Read, traverse |
| `config` | UID 10001; UID 10002; UID 10003 | 10001 Modify; 10002 Read, traverse; 10003 Read, traverse |
| `secrets` | UID 999; UID 10001; UID 10002; UID 10003 | Read and traverse only for all four; dataset owner alone may create/replace files |
| `caddy-data` | UID 10002 | Full Control / Modify, traverse, inherit |
| `caddy-config` | UID 10002 | Full Control / Modify, traverse, inherit |

The frontend has no bind mount and needs no dataset ACE. Docker mounts only the
specific secret files declared for each service; the shared read ACL lets those
non-root identities read their mounted file without granting any service the
whole secrets dataset in the container.

The API and worker write as `10001:10001`; the backup/restore workload writes as
`10003:10003`. Apply both numeric user ACEs to `logs` before installing the App.
Do not create similarly named TrueNAS users as a substitute, and do not grant
Everyone@ write access.

After uploading secrets, confirm the files are not writable by the container
UIDs. The `tls.key` file is sensitive even when the internal-CA configuration is
not selected. Empty `tls.crt` and `tls.key` placeholders are expected for
internal-CA and public-ACME modes.

If PostgreSQL reports `Permission denied`, stop the App in **Apps**, correct UID
999 on the empty/new dataset, and restart it from the UI. PostgreSQL must be
able to apply its owner-only mode bits, so use a POSIX owner/group of `999:999`
for the `postgres` child dataset rather than an NFSv4 ACL that blocks `chmod`.
Do not make a database dataset world-writable.

The same `rate-source-artifacts` ACL protects uploaded utility-bill originals
and sanitized extraction evidence. Never add frontend UID 101 or gateway UID
10002 to that dataset, and never publish it as a share.
