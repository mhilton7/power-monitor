# Firmware management

Upload an already signed manifest and binary with version, notes, hardware target, protocol range, SHA-256, Ed25519 signature, and signing-key ID. The server bounds the upload to 32 MiB, recomputes the hash, verifies the canonical manifest signature, and stores the file outside PostgreSQL. The signing private key should remain offline.

After verification, an administrator explicitly targets compatible devices and schedules a development, canary, or stable deployment. Hardware and protocol ranges are enforced. A device can download only a release assigned to its permanent identity. State tracks scheduled, available, downloaded, installed, validated, failed, and rollback evidence.

Start with one development device, then a canary set, observe heartbeats/storage/sync for an appropriate interval, and only then promote stable. A failed signature, incompatible target, hash drift on disk, or absent deployment denies download. Preserve old signed releases needed for rollback and back them up.
