# Custom roles

Open **Administration > Users & Access > Roles**. Built-in roles are stable and read-only. Select **Clone** to use one as a starting point, or **Create role** for a new definition.

Role names are unique. The editor groups the server-provided permission catalog, automatically adds required dependencies, and prevents selecting permissions the acting administrator does not hold. Protected permissions require explicit confirmation and short-lived reauthentication.

Saving an assigned role creates a new immutable role revision and revokes every affected user's active sessions. The dialog shows the impacted-user count before saving. A stale expected revision is rejected rather than overwriting another administrator's change.

Only unused custom roles can be archived. Archiving retains role revisions, assignments in historical audit records, and attribution; it prevents new assignments and editing. Reassign active users before archival.
