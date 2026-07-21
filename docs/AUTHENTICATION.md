# Browser authentication

Power Monitor uses the existing `POST /api/v1/auth/login` endpoint, opaque
server-side sessions, Secure/HttpOnly/SameSite cookies, CSRF proofs for
authenticated mutations, rate limiting, account safeguards, and optional TOTP.
The browser form does not replace any of those controls.

## Native sign-in contract

The rendered sign-in page contains one `method="post"` form with
`id="login-form"` and `autocomplete="on"`. Its native fields are stable across
renders and releases:

| Purpose | ID | Name | Type | Autocomplete |
| --- | --- | --- | --- | --- |
| Account email | `login-username` | `username` | `email` | `username` |
| Current password | `current-password` | `password` | `password` | `current-password` |
| Optional TOTP | `login-totp` | `totp_code` | text/numeric keyboard | `one-time-code` |

An email address is the account identifier, but `autocomplete="username"` is
intentional: password managers use that token to pair the identifier with the
current password. Editable labels never become IDs or names. Each visible label
is associated using `for` and `id`, the submit control is a native
`type="submit"` button, and Enter from a credential input submits the form.

At submit time the frontend reads `FormData` from the actual form. It maps the
native `username` value to the existing API's `email` property and sends the
password exactly as present in the DOM. It does not trim, normalize, log, or
persist the password. This protects submission when a browser populated the DOM
without updating React state.

The Show/Hide Password button is `type="button"`. It changes the type of the
same native input and preserves its name, autocomplete purpose, value, and text
selection.

## First run and neighboring forms

First-run administrator creation remains a separate `bootstrap-form`. The new
account password uses `autocomplete="new-password"`; the one-time setup token
uses `one-time-code`. User-creation passwords also use `new-password`, protected
administrator reauthentication uses `current-password`, and TOTP fields use
`one-time-code`. These forms continue to use their existing endpoints and
server-side authorization rules.

## Interface text and failures

Compiled login defaults render immediately. Published interface text may update
the heading, labels, help, and button copy, but the form and native credential
nodes are not keyed by the text revision and are not remounted. Because the
credential fields are uncontrolled, a public-text response or unrelated
rerender cannot replace browser-populated values. A failed public-text request
leaves the default form usable.

On authentication failure, the same form remains available, the safe server
problem is announced, and fields reference that error. On success, the session
query is refreshed and the application navigates away so the completed login
form leaves the DOM. MFA, lockout, rate-limit, session, and CSRF behavior is
unchanged.

Power Monitor never stores a browser-saved password in `localStorage`,
`sessionStorage`, IndexedDB, application cookies, interface settings, URLs,
logs, analytics, or screenshots. Saving and updating credentials remains the
browser password manager's responsibility.

