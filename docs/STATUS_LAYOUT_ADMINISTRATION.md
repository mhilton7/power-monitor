# Status Indicators & Layout administration

Open **Administration > Status Indicators & Layout**. Users with
`status_indicators.view` can inspect the published registry, effective layout,
scope precedence, exclusions, current preview, and revision history. Users with
`status_indicators.manage` can edit a draft, but the server remains the final
authority for every key, zone, role, permission, breakpoint, and limit.

Choose a page, optional role, and breakpoint scope. Search or filter by
category, visibility, or zone. For an enabled indicator, choose its semantic
zone, density, and supported content fields. Reorder with drag-and-drop or the
keyboard-equivalent **Move to beginning**, **Move up**, **Move down**, and
**Move to end** controls. Disabled indicators move to the dedicated tray and
can be restored without reconstructing their metadata.

The safe change workflow is:

1. Enter a reason and edit the in-memory configuration.
2. Use the responsive preview at desktop, tablet, or mobile size. Exercise the
   default, empty-zone, one-/two-item, warning, critical, many-item, and long-label
   scenarios when making broad changes.
3. Save the draft. A draft records its base published revision and increments an
   optimistic draft revision.
4. Preview the saved current draft. A changed draft must be previewed again.
5. Publish. Stale base revisions return a conflict rather than overwriting a
   concurrent administrator. Publication creates an immutable revision and
   atomically changes the current pointer.

Hiding a critical indicator prompts for explicit confirmation and displays its
mandatory alternate path. Restore creates a new immutable revision derived from
the selected old revision; it never edits historical evidence. **Reset current
page** removes the page overrides, while **Reset all defaults** creates a draft
from compiled server defaults. JSON import validates into a draft only and can
never bypass preview/publish. Export contains schema and registry versions for
review and source control.

All save, discard, publish, reset, import, restore, visibility, movement,
density, and content changes are audited with actor, timestamp, reason, scope,
old/new values, and revision linkage. Presentation changes never pause monitoring
or alerts.

