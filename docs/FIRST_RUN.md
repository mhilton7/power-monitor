# First run

1. Open the configured HTTPS URL. No default account exists.
2. Create the initial administrator using `BOOTSTRAP_SECRET`. After success, remove that value from `.env` and recreate the API/worker containers; bootstrap cannot run after a user exists.
3. Confirm `Upland Site`, `America/Los_Angeles`, and Southern California Edison defaults. Edit permitted polling CIDRs before adding a pull/hybrid address.
4. Create a utility account without exposing its full account number. Set the billing-cycle day or explicit meter-read dates.
5. Select the exact plan code shown on the bill. Review the effective date, checked date, and official link. Activate a rate version only after the values are checked.
6. Keep a one-CT sensor at `energy_only`. Use `full_account` only for an explicitly configured non-overlapping whole-account aggregate; only that scope may receive fixed charges and baseline credits.
7. Create circuits and an aggregate set. Resolve every parent/child overlap warning before confirmation.
8. Create a 10-minute, single-use enrollment token and enter it into the sensor or simulator with this server's validated HTTPS URL.
9. Verify the signed first heartbeat: permanent ID, address source/history, live measurement, PZEM, microSD, trusted time, and firmware.
10. Confirm history backfill reaches `online_synchronized` and that no unexplained sequence gap remains.
11. Run and verify a backup: `path=$(./scripts/backup.sh | tail -1); ./scripts/verify-backup.sh "$path"`.

See [SCE setup](SCE_SETUP.md), [Enrollment](DEVICE_ENROLLMENT.md), and [Backup and restore](BACKUP_AND_RESTORE.md).
