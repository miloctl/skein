# Production readiness TODO

This file is intentionally untracked.

## Current restrictions

- Do not deploy the current OIDC browser flow to production.
- Do not distribute current-source artifacts under the published `0.3.2` identity.
- Keep browser credentials out of new code. Personal API keys remain automation credentials.

## 1. Publish `0.3.3` for staging

Use `0.3.3` as an immutable checkpoint for the completed hardening work. Do not promote this release to production.

- [ ] Complete the `CHANGELOG.md` Unreleased sections.
- [ ] Remove shipped work from `docs/ROADMAP.md`.
- [ ] Run:

  ```sh
  python3.12 scripts/prepare-release.py 0.3.3
  ```

- [ ] Review every generated change.
- [ ] Run all release gates in `RELEASING.md`.
- [ ] Commit and push from an authenticated terminal.
- [ ] Approve the PyPI and npm publication environments.
- [ ] Finalize the release from the original tested artifact.
- [ ] Confirm that annotated tag `v0.3.3` points to the published release commit.
- [ ] Pull the exact wheel and npm tarballs from the registries.
- [ ] Put the three Skein artifacts in one read-only directory.
- [ ] Generate `SHA256SUMS` for that directory.
- [ ] Update Atlas and Charizard artifact names, Python locks, npm locks, image tags, and image digests.
- [ ] Run both artifact-only consumer contracts against the same artifact directory.

## 2. Implement the BFF for `0.4.0`

Treat this as an authentication and deployment-contract change.

### Backend session

- [ ] Add a server-held browser session.
- [ ] Store provider access and refresh tokens only on the backend.
- [ ] Encrypt stored provider tokens with a key from an environment Secret.
- [ ] Store only a hash of the opaque browser session identifier.
- [ ] Rotate the session identifier after sign-in and refresh.
- [ ] Revoke the server session during sign-out.
- [ ] Add absolute and idle session expiration.
- [ ] Keep the verified OIDC `(iss, sub)` binding as the identity authority.
- [ ] Return identity metadata only. Never return provider tokens.

### Cookie and CSRF controls

- [ ] Set the session cookie to `HttpOnly`.
- [ ] Set the session cookie to `Secure`.
- [ ] Set the session cookie to `SameSite`.
- [ ] Scope the cookie to the backend host and required path.
- [ ] Add CSRF protection to every cookie-authenticated write.
- [ ] Refuse writes with a missing or invalid CSRF value.
- [ ] Keep CORS credential settings limited to the configured frontend origin.

### Browser changes

- [ ] Remove OIDC access and refresh tokens from `localStorage`.
- [ ] Remove personal API-key persistence and fallback in OIDC mode.
- [ ] Hide the browser API-key form in OIDC mode.
- [ ] Keep personal API keys for CLI, hooks, and automation.
- [ ] If browser key sign-in remains necessary, exchange the key once for the server-held session.
- [ ] Keep refresh single-flight and preserve sign-out generation invalidation.

### BFF acceptance gates

- [ ] Confirm that browser storage contains no provider token or personal API key.
- [ ] Confirm that token exchange responses contain no provider token.
- [ ] Confirm all required cookie attributes in a real browser.
- [ ] Confirm that a write without CSRF protection is refused.
- [ ] Confirm refresh behavior under concurrent requests.
- [ ] Confirm session expiry and server-side revocation.
- [ ] Confirm sign-out invalidates the server session.
- [ ] Confirm a backend restart preserves or safely invalidates sessions.
- [ ] Run signed browser tests for sign-in, refresh, expiry, sign-out, CSRF, and concurrent requests.
- [ ] Run a security review before release preparation.
- [ ] Prepare and publish `0.4.0` through the protected release process.

## 3. Implement workplace directory adapters

Complete this work in each private workplace package.

- [ ] Replace the fail-closed example resolver with an authoritative server-side directory lookup.
- [ ] Resolve the canonical Skein user to current employment status and groups.
- [ ] Return no record for an unknown user.
- [ ] Return no record when the directory is unavailable.
- [ ] Do not restore groups from a hardcoded username.
- [ ] Use a short and explicit cache lifetime.
- [ ] Keep requester and approver identities separate.
- [ ] Test that a removed group removes approval authority.
- [ ] Test that an inactive user cannot approve.
- [ ] Test that a directory outage keeps the action in review.
- [ ] Test that an unknown user fails closed.
- [ ] Test that a requester cannot approve their own action.
- [ ] Test that a stale token group cannot override the current directory result.
- [ ] Publish a workplace package patch release when the adapter is ready.

## 4. Migrate existing OIDC users

Before the production OIDC cutover:

- [ ] Collect the authoritative OIDC subject for each existing roster user.
- [ ] Bind each user with:

  ```sh
  SKEIN_AUTH_MODE=oidc SKEIN_OIDC_ISSUER=https://idp.example.com \
    python -m app.bind_oidc '<subject>=<existing-user>'
  ```

- [ ] Confirm that every existing user resolves through `(iss, sub)`.
- [ ] Confirm that renamed username claims keep the same Skein identity.
- [ ] Confirm that a different subject cannot claim an existing roster name.

## 5. Production go/no-go

Production is permitted only when all items that follow are true:

- [ ] Skein `0.4.0` contains the server-held BFF session.
- [ ] Atlas and Charizard use exact published `0.4.0` artifacts.
- [ ] Both consumers use one reviewed `SHA256SUMS` artifact set.
- [ ] Both consumer-owned source and artifact contracts pass.
- [ ] The authoritative workplace directory adapter is live and tested.
- [ ] Existing OIDC users have explicit subject bindings.
- [ ] Final backend and frontend images use reviewed registry digests.
- [ ] Browser tests show no credentials in JavaScript-accessible storage.
- [ ] CSRF, refresh, expiry, sign-out, and revocation tests pass.
- [ ] Full backend, frontend, deployment, image, upgrade, and dependency gates pass.
- [ ] Three final read-only reviews report no additional production blocker.
