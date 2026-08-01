# Device sequence cursor contract

Heartbeat responses retain the legacy top-level
`highest_contiguous_accepted_sequence` and add a backward-compatible
`sequence_cursor` object:

```json
{
  "highest_contiguous_accepted_sequence": 785,
  "sequence_cursor": {
    "highest_contiguous_accepted_sequence": 785,
    "maximum_seen_sequence": 790,
    "next_sequence_floor": 791
  }
}
```

The contiguous value is the durable synchronization acknowledgement. The
maximum-seen value is the largest immutable `(device_id, sequence)` already
known by the server and may be higher when gaps exist. Replacement storage must
choose a new sequence above both values. `next_sequence_floor` is server-derived
as `maximum_seen_sequence + 1` and is never a permission to discard gaps or
fabricate readings.

The server determines device connectivity from receipt of a valid signed
heartbeat. Storage details independently produce `online_storage_reconciling`
or `online_storage_degraded`; neither is reported as offline. Durable History
continues to depend on accepted reading batches.

Operators receive specific, actionable notifications for sequence
reconciliation, continuity restoration on a blank or replaced card, and cursor
conflicts. These conditions do not masquerade as network outages. The generic
microSD failure rule is reserved for actual unavailable or unwritable storage.
