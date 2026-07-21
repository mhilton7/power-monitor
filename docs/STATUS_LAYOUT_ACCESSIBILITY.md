# Status layout accessibility

Status Indicators & Layout follows the existing keyboard and screen-reader
patterns. Every drag operation has visible first/up/down/end buttons. Changes
are announced through a live region, focus remains on the selected item after a
move where possible, and native form controls retain visible `:focus-visible`
styles. Color is never the only signal: rendered values include readable
severity text and accessible names.

Indicator content options are independent. If the visual label is hidden, the
indicator still exposes `Label: value` as its accessible name. Icons are
decorative, descriptions and details remain available as tooltips when enabled,
and detailed density can expose freshness without changing the underlying
status. The disabled tray and each semantic zone use headings and landmarks
that make the configuration understandable without spatial drag gestures.

The responsive renderer is tested at desktop, tablet, and mobile widths, at
empty/one/two/three/four/many-item counts, with long labels, and without
horizontal document overflow. Empty zones are absent from the accessibility
tree. Reduced-motion users do not depend on animation to understand reflow.
Preview scenarios use synthetic display strings only; they do not create fake
readings or modify production status.

