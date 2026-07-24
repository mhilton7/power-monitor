# Rate versioning and recalculation

Every calculation stores an immutable `rate_version_id` and calculation-engine
version. Assignments are effective-dated. If a requested interval crosses an
assignment boundary, the server creates separate calculation runs so each
segment uses the exact applicable version.

Activation supersedes the prior active version prospectively and moves current
account assignments. A retroactive activation queues replacement estimates
only for overlapping, completed runs whose billing cycle is not finalized.
Finalized cycles and their historical results remain unchanged.

Account administrators use **Billing > Utility Accounts** to create or
schedule an assignment. The server locks that account while checking the
effective window, rejects overlap, records the reason and actor, and preserves
the prior assignment/version IDs. Closing an open prior window at the new
boundary does not replace its rate-version reference or calculated evidence.
The Rates page's **Published · Available** state is library readiness only;
**Effective now · account/site** is a real account assignment.

Rollback is prospective: clone or select the prior normalized version, create
a new effective-dated version, validate it, and activate it. Never modify an
active or already-used row. After a parser change, keep the old adapter/version
available for evidence reproducibility, run its fixture suite, reprocess the
archived artifact into a new extraction, and review the resulting candidate.
