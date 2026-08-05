# TODO

Deliberate debts — things we decided to ship without, on purpose, with the
condition that brought them here. Backlog/ideation lives in docs/ROADMAP.md;
this file is only for accepted trade-offs that must eventually be repaid.

- **Harden field-guide identity.** `GET /api/field-guide` (and `/hint`)
  ride weak `X-User` identity — a spoofed header can read a teammate's
  untied cards, consume their "newly tied" state, and (via the rate-capped
  dismiss endpoint) permanently silence their weekly suggestions — a write
  into their state, not just a read. Accepted 2026-07-31
  under the trusted-network model (same exposure class as the My Day briefing;
  truly private surfaces are excluded from the guide entirely — see
  docs/FIELD-GUIDE.md "Weak identity accepted"). Repay when the OIDC
  identity bridge lands: once every request carries a validated identity,
  move the guide (and the other personal-but-not-private surfaces) onto it
  instead of inventing a bespoke auth layer now. The 2026-08 buzz work grew
  this class: the activity feed's anti-surveillance scope, chat threads and
  their engagement links, and /api/usage all key on the same weak header —
  the feed matters most, because its "teammates' rows never appear"
  guarantee is only as strong as X-User until the bridge lands.

  Narrowed 2026-08-02: the bridge landed (`SKEIN_AUTH_MODE`). In api-key
  and oidc modes the header is never consulted — every request carries a
  validated identity, so this whole exposure class closes there with no
  per-surface work. The debt now applies to trusted-header deployments
  only, and repays fully when the frontend sign-in flow (docs/ROADMAP.md)
  lets a team leave that mode.

- **No re-baseline path for a legitimate unchained activity row.** When
  log_activity's standalone append fails, the row is recorded unchained (a
  write must not 500 over bookkeeping) — and an unchained row above the
  pre-036 baseline fires `activity_chain_broken` on every weekly re-fire,
  indistinguishable from a smuggled row. Dismissal only suppresses 28 days;
  the sole true fix is hand-editing the baseline in app_settings. Accepted
  2026-08-02 because the fallback has never fired outside tests and a
  re-baseline operation is attacker-usable machinery we should not build
  speculatively. Repay when the first real fallback fires in production:
  add a logged, StrongUser re-baseline that records the reason in the
  ledger itself.

  Narrowed 2026-08-02: the baseline now rides on every anchor-log line, so
  the hand-edit is no longer SILENT — `check_anchor_log` reports a baseline
  above the highest ever anchored, and says plainly that a smuggled row and
  a legitimate fallback append look the same from there. The debt that
  remains is only the missing operation to tell those two apart.

- **Single-replica posture is implied, not enforced or insured.**
  Process-local rate limits, in-process locks, `claim_job`, and SQLite's
  single writer are one deliberate fact: this design runs as one replica,
  which is correct at team scale. Accepted until the OpenShift manifests
  exist. Repay inside the deploy work: `replicas: 1` + `strategy:
  Recreate` + one PVC, with a manifest comment naming every mechanism that
  assumes it; a Litestream sidecar (container, not a code dependency)
  streaming the SQLite WAL to S3-compatible storage, restore drill in the
  runbook; `SKEIN_TRUST_PROXY_HOPS=1` behind the router;
  `SKEIN_CORS_ORIGINS` set to the exact browser origin (scheme + host +
  port — the live test showed 127.0.0.1 vs localhost is already a
  mismatch); memory requests/limits sized for `next start` + uvicorn (the
  dev frontend OOM-crashed at ~heavy parallel load, which a container
  limit turns into a clean restart instead of a wedge);
  `SKEIN_AUTH_MODE` oidc or api-key, trusted-header staying dev-only; and
  a `v*` release tag on the deployed commit — the tag is what activates
  the upgrade-path CI job (scripts/upgrade-path.sh baselines on the
  newest one) and is the moment migrations stop being editable.

- **Dependency-update noise is handled by hand.** The `npm audit`
  high-severity in `brace-expansion` is dev-only and pre-existing; triaging
  its successors one by one is a treadmill. Repay with Renovate on the
  Gitea instance (grouped weekly PRs) — instance configuration, so it is
  whoever runs the Gitea's to flip on. The CI half landed 2026-08-04:
  `npm audit --omit=dev` gates pushes, so production dependencies block
  and dev-only noise does not.

- **`semantic_search` scans the whole embeddings table per request.**
  Deferred at the query site (services/search.py) and genuinely fine at
  team scale (~50ms at 10k rows). The chosen fix, so nobody re-litigates
  it: `sqlite-vec` — pip-installable loadable extension, KNN inside SQLite,
  WAL-compatible — not a hand-rolled cached matrix, and not an external
  vector store, which would break keyless-first. Repay when embeddings are
  enabled in a real deployment or the table passes ~50k rows.

Decided against, so the next review does not re-open them: Postgres (wrong
scale — the services layer keeps the door open), Redis-backed rate limits
(per-pod buckets are fine at one replica), a migration framework (the
40-line runner plus tests/test_migrations.py cover the classes that matter
here), and suite-wide mutation testing (the per-fix discipline — break the
fix, watch its pin fail, restore — is sharper and cheaper).
