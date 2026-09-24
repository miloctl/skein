# Browser sessions

Skein keeps browser authentication on the server. The browser receives an opaque session cookie and identity metadata, not provider access or refresh tokens. An explicitly entered API key is exchanged once and is not saved in browser storage.

## Deployment requirements

- Use HTTPS for the frontend and API. Both must be on the same schemeful site. Separate origins such as `https://skein.example.com` and `https://api.example.com` are supported.
- Set `SKEIN_CORS_ORIGINS` to the exact frontend origin. A wildcard is not accepted for browser sessions.
- If the frontend and API are on different sites, provide same-origin routing at the deployment boundary. Skein does not add a second authentication service in Next.
- For OIDC browser sign-in, put a valid Fernet key in `SKEIN_CREDENTIAL_KEY` in the deployment Secret. Keep the key across ordinary restarts. Changing it invalidates existing OIDC browser sessions and makes personal MCP credentials sealed with the old key unreadable.
- A missing or invalid sealing key disables OIDC browser exchange only. Direct bearer authentication and API-key-backed browser sessions remain available.
- For local development on `http://localhost`, Chromium and Firefox accept the `Secure` session cookie. Safari does not, so sign-in there needs HTTPS.
- The supported backend deployment remains one replica with Recreate upgrades. This feature does not provide multi-replica chat, scheduler, artifact, or rate-limit coordination.

Do not put credentials in a ConfigMap, frontend build variable, or `app_settings`.

## Sign-in and sign-out

The current authorization-code and PKCE browser flow remains. The browser checks its tab-local state. The server checks the request Origin and requires the callback to be that origin's `/auth/callback` before exchanging the code.

A successful exchange sets `__Host-skein-session` with `Secure`, `HttpOnly`, `SameSite=Lax`, and `Path=/`, without a Domain attribute. The session has an absolute eight-hour lifetime. It does not slide with activity. Each person can have at most twelve active sessions.

The database stores a hash of the cookie value. OIDC access and refresh credentials are Fernet-sealed. An API-key session stores the active key ID, not the raw key. Protected requests still check the current person, key state, identity walls, and policy. A rename follows the stable user ID. Deactivation and merging away an identity remove its sessions, so reactivation cannot restore them.

The server refreshes expired OIDC authority outside database transactions. A bounded lease permits one refresh at a time. A refresh result must still match the session, subject, issuer, client binding, expiry, and lease before it is stored. A transient provider failure leaves the session available for retry. The session lifetime does not extend an access token's authority.

Sign out revokes the server session and expires the cookie without contacting the identity provider. It does not end the identity provider's own sign-in session. Browser tabs serialize requests that change cookies. An old callback cannot restore a completed logout.

## Browser request contract

- `GET /api/auth/config` returns public deployment and sign-in configuration. `browser_session_error` reports browser-specific configuration faults without changing the REST authentication mode.
- `GET /api/auth/session` returns uncached identity, strength, authentication method, and CSRF metadata. It does not refresh the provider token. An anonymous response can carry metadata for clearing an expired cookie, but grants no authentication.
- `POST /api/auth/token` accepts an authorization code, verifier, and pinned callback URI. It returns session metadata, never provider tokens. Browser-supplied refresh tokens are refused.
- `POST /api/auth/session/key` accepts a deliberately entered personal API key once and returns the same session metadata.
- `DELETE /api/auth/session` revokes the session and clears the cookie. It requires an approved Origin and the cookie-bound CSRF value when a cookie exists.
- `DELETE /api/auth/sessions` revokes every browser session of the cookie's person, this one included, and clears the cookie (Settings → Sign out of every browser). It always requires an approved Origin and the cookie-bound CSRF value. API keys are not touched.

Browser fetches use `credentials: include`. Every protected cookie-authenticated request carries `X-Skein-CSRF`, including reads, uploads, downloads, chat streams, and page-close preference saves. This is also an identity binding: a stale tab cannot send work as a different account after the cookie changes.

A mismatched binding refuses the request. The browser does not replay a mutation under a replacement identity. It clears identity-scoped caches and mounted content before new requests. Ordinary authentication failures and metadata responses never clear cookies, because a late response could otherwise clear a newer login.

Cookie-authenticated requests cannot mint reusable API keys. Key creation through a direct bearer-authenticated automation client remains available. Browsers retain key metadata and revocation controls, not the key-minting result.

Legacy browser token and personal-key storage is cleared on load. There is no automatic credential exchange or fallback. Users must sign in again after upgrading.

## Recovery and limits

Skein database dumps and the public-schema mirror keep the browser-session schema but exclude its rows. Database recovery therefore requires a new browser sign-in. Personal MCP ciphertext remains in database backups as before. Portable JSON exports exclude browser sessions entirely. If an operator makes a separate full database copy outside Skein, remove restored browser-session rows before reopening that copy to users.

`HttpOnly` limits credential theft by injected scripts. It does not prevent such a script from making requests as the current browser user. Input validation, output encoding, authorization, and browser security headers remain necessary.

Local HTTP loopback exceptions for Secure cookies differ by browser. Use local HTTPS for cross-browser session tests rather than weakening the production cookie. Browser tabs need Web Locks for coordinated sign-in changes.

Every browser session request has a 60-second deadline: the sign-in configuration read, the session read, the sign-in exchange, and the sign-out. Session reads, sign-in, and sign-out hold one lock across all tabs, so a request that never answered kept every tab at "Checking your browser session" until reload. After the deadline, the page shows the backend-unreachable message and a **Try again** control, and the lock is free. The server bounds its own answer: a request waits up to 30 seconds for a database connection before a busy reply, and the sign-in exchange then makes identity-provider calls that stop after 5 seconds each. The deadline is above both, so a slow answer or a busy reply still arrives. With several tabs open, each tab waits for the lock before its own deadline starts, so the last tab can show the message later.
