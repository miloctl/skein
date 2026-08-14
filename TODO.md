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

- ~~No re-baseline path for a legitimate unchained activity row.~~ Repaid
  2026-08-05, in the opposite direction from the sketch above it: instead of
  a re-baseline (which raises the baseline — the attacker-usable machinery
  the entry itself warned against), `activity.adopt_unchained` chains the
  orphaned rows at the tail, appends a chained `adopt_unchained` receipt naming
  them, and only ever LOWERS the baseline. Runs nightly before verify and
  anchor. The receipt in the feed, checked against the server log's
  'activity chain append failed' warnings, is the operation that tells a
  fallback from a smuggle.

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
  mismatch); memory requests/limits sized for `next start` + uvicorn —
  measure those two, never the dev server: `next dev` idles at ~2.3 GB RSS
  and crashed twice on Node's default ~4.2 GB old-space cap during bursts
  of edits, which is why `npm run dev` sets `--max-old-space-size`. That
  number must NOT be copied into the container: a heap cap above the
  cgroup limit makes Node grow into an OOM-kill instead of collecting
  harder. Growth per compile measured at ~3 MB/page, so there is no leak
  to size around;
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

- **The extension contracts verify a change only after it reaches main.**
  `.gitea/workflows/ci.yml` is push-only because the runner shares a host with
  a live deployment, so it must never execute untrusted pull_request code.
  The four reference-contract scripts therefore run after the merge, and the
  gate a change meets first is `scripts/hooks/pre-push` (lint plus the whole
  pytest suite). Accepted 2026-08-13 while the extension boundary has one
  author and one deployment: the fast half of the protection —
  `tests/test_release_contract.py` pinning the frozen 1.0 import surface, and
  `tests/test_import_boundary.py` — already runs in that hook, and the slow
  half needs wheels, npm, kubectl, and docker. Repay by provisioning an
  ephemeral sandboxed runner, then adding the `pull_request` trigger to the
  `extension-contracts` job alone; the review that asks for a PR trigger
  without that runner is asking to run untrusted code beside the deployment.

- **The extension-store backup registry is one per process, not one per
  application.** `services/admin.py::set_extension_stores` holds a module-level
  dict that `create_app` replaces on every call, and the daily backup job reads
  it with no application handle. One composed app per process is correct in
  production, and it is what every other single-replica mechanism here already
  assumes. It is wrong in a test session: an extension repository that follows
  docs/EXTENSIONS.md and starts a composed app with `TestClient` can have its
  stores cleared by a later `create_app()` in the same session, so its backup
  test passes or fails on test ordering. Accepted 2026-08-14 because the fix
  wants the stores on `app.state` and the job has no handle to reach them.
  Repay by giving the core backup job its composed registry the way
  `skein.core.agent-run` already receives one (`app/main.py`), then deleting
  the module-level dict.

- **A contributed route's background task outlives its work grant.**
  `extension_route_services` revokes the grant in its dependency teardown, and
  FastAPI runs that teardown after the response — but Starlette runs
  `Response.background` before it, so a `BackgroundTasks` task still writes
  core rows under the route's provenance. Every other case is closed: a
  stashed context, a spawned thread, a handler that raised, and use after a
  streaming body all return `EXECUTION_CONTEXT_CLOSED`. Accepted 2026-08-14
  because the fix wants a middleware that appends the close after the
  handler's own tasks, and middleware ordering is the wrong thing to change in
  a release that already moves the write path. docs/EXTENSIONS.md tells
  authors to use a `JobContribution` instead. Repay by appending the close as
  the last background task from a response middleware, then deleting that
  paragraph.

- **A held public command is queued once per retry.** `_held_error` files a
  fresh `pending_changes` row and a team notification on every refused
  attempt, and contributed routes do not pass through `ratelimit.check`. An
  unattended integration that retries fills the queue its reviewers work from.
  Accepted 2026-08-14: the documented pattern is to record the `review_id` and
  skip what was already asked about. Repay by deduplicating on the namespace,
  command, resource, and argument hash while a proposal is pending, returning
  the existing `review_id` instead of a second row.

- **The reviewed invocation is not bound to the policy context that gets
  re-validated.** For a held public command, `_current_extension_review`
  returns the saved `PolicyInput` and never compares it against
  `extension_review_invocations.invocation`, so the two columns can drift and
  the drift executes. The core proposal path builds an expected payload from
  the row and refuses a mismatch. Accepted 2026-08-14 because there is no
  external write path to that table today — the only writers are the propose
  call and the status updates beside it, extension stores are separate
  databases, and `ATTACH` is refused — so this is a missing integrity binding
  rather than a live exploit. Provenance no longer depends on it: the resumed
  actor comes from the refreshed subject. Repay by hashing the invocation into
  the saved policy context at propose time and comparing at approve time, the
  way the core path compares its contract.

Decided against, so the next review does not re-open them: Postgres (wrong
scale — the services layer keeps the door open), Redis-backed rate limits
(per-pod buckets are fine at one replica), a migration framework (the
40-line runner plus tests/test_migrations.py cover the classes that matter
here), suite-wide mutation testing (the per-fix discipline — break the fix,
watch its pin fail, restore — is sharper and cheaper; `scripts/mutation-test.sh`
runs the same discipline with tooling, on the module in front of you), and
load or contention testing (an internal tool at one replica; the 429/503
mapping is covered functionally, and the first real contention incident is
what would justify a rig).
