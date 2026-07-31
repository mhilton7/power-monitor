# Browser and password-manager compatibility

The sign-in form uses standard native HTML semantics for current Chrome,
Chromium-based Edge, Firefox, and Safari. Automated Chromium tests verify the
DOM contract, direct native-value submission, click and Enter submission,
same-node password visibility, responsive layout, focus, and theme styling.
Headless automation does **not** prove that the real Chrome Password Manager UI
offered to save or update a credential.

## Use one stable HTTPS origin

Password managers associate credentials with the exact site being visited. Pick
one stable production HTTPS URL, configure `PUBLIC_ORIGIN` to that exact origin,
and use it consistently. For example, a local deployment might use a LAN DNS
name and non-default port such as `https://power-monitor.home.arpa:8443`; this is
an example, not a hard-coded application value.

A credential saved for that URL may not appear at a raw IP address, over HTTP,
at another hostname, on another port, or on a development origin. Power Monitor
does not attempt to share credentials across unrelated origins. Keep TLS
verification enabled and trust the configured Caddy/internal CA as documented
for TrueNAS.

## Chrome settings and saved-entry repair

Use a current stable Chrome profile in which Google Password Manager is allowed
to offer to save passwords and to sign in automatically if that is your chosen
browser policy. Confirm that the exact Power Monitor URL is not in Chrome's
declined/never-save list. Enterprise-managed profiles may enforce different
behavior; consult the browser policy administrator rather than weakening TLS or
other security settings.

To correct a bad synthetic/test credential, open Google Password Manager,
locate the entry for the exact Power Monitor hostname and port, and edit or
delete only that entry. Return to the same HTTPS URL, sign in with the corrected
test credential, and accept Chrome's save/update prompt. Do not export or place
production administrator passwords in test evidence.

## Manual Chrome acceptance test

This must be completed with a non-production test account on the exact
production-like HTTPS origin before claiming real password-manager validation:

1. Enable password saving/autofill in current stable Google Chrome.
2. Open the exact configured Power Monitor HTTPS URL.
3. Confirm the email and password fields are empty, visible, and labeled.
4. Sign in manually with the synthetic test account.
5. Accept Chrome's offer to save the credential.
6. Sign out.
7. Return to the same exact URL.
8. Confirm Chrome offers or fills the saved account.
9. Select the saved credential.
10. Confirm both email and password appear and remain readable.
11. Click **Sign in** without typing and confirm authentication succeeds.
12. Sign out, select the saved credential again, and press Enter from the password field.
13. Confirm the Enter-key sign-in succeeds.
14. Publish customized login heading and field labels through **Dashboard & Login Text**.
15. Sign out and confirm autofill still works with the customized labels.
16. Reload while public interface text is loading and confirm the filled values remain.
17. Toggle **Show password** and **Hide password** and confirm the value remains.
18. Change the synthetic account password through the supported account workflow.
19. Confirm Chrome offers to update and then uses the new saved password.
20. Repeat at mobile width and, when practical, in Chromium-based Edge.

Record only the browser version, tested origin, pass/fail result, and sanitized
observations. Never record the password or include it in a screenshot.

## Dashboard typography and History charts

Production dashboard text uses the native system UI stack:
`ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`.
It does not alias an installed font under a custom family name, which previously
made glyph metrics and heading spacing vary by browser and operating system.
Page titles use `1.08` line height, `-.015em` letter spacing, `.04em` word spacing,
and balanced wrapping without inserting literal spaces.

The Playwright matrix retains the existing dark/light desktop, tablet, and mobile
coverage and also runs native Microsoft Edge, desktop Firefox, and WebKit. History time labels are produced
from explicit server interval timestamps and the configured Home timezone rather
than the browser timezone. Manual review should confirm page-title wrapping,
15-minute tick labels, exact interval tooltips, visible axis titles, gap notices,
and chart-color controls in current Chromium, Edge, Firefox, and Safari/WebKit.

