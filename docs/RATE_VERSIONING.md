# Rate versioning and recalculation

Every calculation stores an immutable `rate_version_id` and calculation-engine
version. Assignments are effective-dated. If a requested interval crosses an
assignment boundary, the server creates separate calculation runs so each
segment uses the exact applicable version.

Activation supersedes the prior active version prospectively and moves current
account assignments. A retroactive activation queues replacement estimates
only for overlapping, completed runs whose billing cycle is not finalized.
Finalized cycles and their historical results remain unchanged.

Rollback is prospective: clone or select the prior normalized version, create
a new effective-dated version, validate it, and activate it. Never modify an
active or already-used row. After a parser change, keep the old adapter/version
available for evidence reproducibility, run its fixture suite, reprocess the
archived artifact into a new extraction, and review the resulting candidate.
