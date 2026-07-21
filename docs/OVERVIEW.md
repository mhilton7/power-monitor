# Overview

The Overview is the selected site's at-a-glance operational workspace. Its
default title and subtitle remain editable under **Administration > Dashboard &
Login Text**. The default subtitle is: “Monitor energy use, costs, device
status, and site performance in one place.”

The page uses one compact site-state strip, one Live Power hero, a configurable
**Site Summary**, Device contribution, Operational status, and the current
device cards. Live Power is authoritative only after at least one included
sensor sends a valid signed heartbeat. A reported zero is displayed as zero;
missing heartbeats or readings are displayed as unavailable.

The Site Summary is the `overview_site_summary` semantic layout zone. Its
recommended defaults are Energy today, Estimated today, Billing-cycle energy,
Cycle estimate, Synchronization, and Active alerts. Administrators can enable,
disable, reorder, change density, preview, publish, and restore these items from
**Administration > Status Indicators & Layout**. Available optional items
include online devices, recent peak, current rate context, data coverage, and
latest backup where the current user's permissions allow them.

A stable `metric_identity` prevents a fact from appearing twice in one resolved
page/role/breakpoint layout. The site selector owns `site.current`; the Live
Power hero owns `power.current`; a configured Recent peak summary replaces the
hero's peak value instead of duplicating it. Empty or suppressed items leave no
grid gap.

When a site has no sensors, the normal cards are replaced by one enrollment
state. When sensors exist but have not reported, the page shows one waiting
state and unavailable live power rather than a grid of false zeros. Once valid
data arrives, the configured summary and contribution sections appear
automatically.
