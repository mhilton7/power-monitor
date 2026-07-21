# Status layout zones and precedence

Layouts use semantic server identifiers rather than CSS coordinates. Global
zones are `global_header_left`, `global_header_center`,
`global_header_right`, `sidebar_upper`, `sidebar_lower`, and `global_footer`.
The former full-width `global_status_row` is retired. Page zones are `page_header_primary`,
`page_header_secondary`, `page_status_row`, `page_summary_strip`, and
`page_footer`. Feature zones are `overview_site_state`,
`overview_site_summary`, `history_context`, and `diagnostics_summary`. Narrow
layouts use `mobile_header`, `mobile_status_strip`, and `mobile_status_drawer`.

An item can be overridden by page, role, and breakpoint. Resolution is
deterministic:

1. An exact role + page override wins.
2. A page override wins over a role-only override.
3. A role-only override wins over the global default.
4. Within the winning scope, an exact `desktop`, `tablet`, or `mobile` override
   wins over `default`.
5. The user's underlying data permission is applied last. Layout visibility
   never grants access.
6. Candidates are grouped by `metric_identity`; the required or strongest
   canonical placement wins and lower-priority duplicates are suppressed.

The selected site remains the existing authorization scope and is passed to the
existing status-value service. Per-user personalization is intentionally
disabled because this release has no compatible preference infrastructure;
the schema permanently records `personalization_enabled: false`.

On mobile, eligible global and page indicators derive into mobile zones unless
an exact mobile override is present. The renderer uses responsive CSS grid with
bounded minimum widths. It supports one, two, three, four, and many items,
compact/standard/detailed density, long labels, high zoom, and browser text
scaling without fixed horizontal positioning.

When no visible items resolve to a zone, the renderer emits no zone wrapper at
all. When one item remains, it receives the useful bounded width of the zone;
siblings reflow immediately when an item is disabled or moved. Disabled items
never leave placeholders, empty grid cells, reserved margins, or spacer
components.
