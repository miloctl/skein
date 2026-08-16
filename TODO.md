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

- ~~Single-replica posture is implied, not enforced or insured.~~ Repaid
  2026-08-14, most of it: `deploy/k8s/` pins `replicas: 1` +
  `strategy: Recreate` + RWO on one PVC, with the manifest comment naming
  every mechanism that assumes one process, and
  `scripts/reference-deployment-contract.sh` renders both overlays in CI
  and fails if any of it is lost. `SKEIN_TRUST_PROXY_HOPS=1`,
  `SKEIN_CORS_ORIGINS`, oidc-mode auth and the memory-limit warning all
  live in the base/overlays; images come from `scripts/publish-images.sh`
  (a `v*` tag remains the release act, RELEASING.md). NOT shipped from the
  sketch: the Litestream sidecar — the base mounts a second PVC as
  `SKEIN_BACKUP_MIRROR` instead, and `deploy/k8s/README.md` forces the
  written choice between independent storage and the reduced guarantee.
  Litestream stays the upgrade path if off-cluster streaming is ever
  wanted; the resource numbers stay starting sizes until measured under
  real load.

- **Dependency-update noise is handled by hand.** The `npm audit`
  high-severity in `brace-expansion` is dev-only and pre-existing; triaging
  its successors one by one is a treadmill. Repay with Renovate on the
  Gitea instance (grouped weekly PRs) — instance configuration, so it is
  whoever runs the Gitea's to flip on. The repo half landed 2026-08-14:
  `renovate.json` (weekly, non-major updates grouped) waits for the
  instance flip. The CI half landed 2026-08-04:
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
  half needs wheels, npm, kubectl, and docker. Accepted 2026-08-13; the
  repayment path opened 2026-08-14 and is not yet closed.

  `.github/workflows/ci.yml` runs the four verification jobs —
  `extension-contracts` among them — on `pull_request`, on GitHub's hosted
  runners. Those are ephemeral and disposable, which is the precondition
  this entry names, so the untrusted-code objection does not apply there.
  What is still open is where a change is actually proposed: a PR opened
  against the GitHub mirror is verified before merge, and one opened on
  Gitea is not. The debt closes when PRs land on the mirror as a matter of
  course, or when Gitea gets a sandboxed runner of its own. Until then the
  first gate a Gitea-side change meets is still `scripts/hooks/pre-push`.
  The Gitea workflow must stay push-only regardless: its runner shares a
  host with a live deployment, and the review that asks for a PR trigger
  there is asking to run untrusted code beside the deployment.

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

- **Skein is English-only and has no locale catalog or language selector.**
  Accepted 2026-08-16 while the English source text is corrected and simplified;
  Do not extract inaccurate copy first. That preserves it behind opaque message keys.
  Revisit when a deployment needs another language or the team selects a second
  supported locale. Add personal UI language and a separate team default for
  shared reports and notifications, keep route paths stable, retain English as
  the fallback, and validate the system with a pseudo-locale before exposing a
  selector. Migrate by surface into feature-scoped catalogs, add stable API error
  codes before translating backend failures, and localize field-guide text by
  knot ID instead of duplicating its registry. User-authored text stays as
  written. Existing hash-chained activity rows cannot be rewritten, and stored
  reports keep the language in which they were generated.

- **The strands-tools orchestration tools are refused without evaluation.**
  `use_agent`, `swarm`, `graph`, `agent_graph`, and `workflow` are absent from
  the `extra_tools.py` allowlist, so no deployment can enable them. Skein's
  own primitives cover the same ground today: `consult_specialist` for
  delegation (with its structural depth cap), flocks for fan-out, and the
  planner sub-agent for decomposition — and the strands versions would bypass
  the governance those primitives carry (`workflow` is also named in the
  exclusion docstring for model-controlled file paths). Accepted 2026-08-16.
  Revisit when a real task needs more than one level of delegation or a
  dynamic agent topology, and evaluate then whether to wrap the strands
  tools in governance or extend `consult_specialist` instead.

- ~~An uploaded file has no retention policy and no delete.~~ The delete half
  is repaid, 2026-08-16, and NOT in the shape this entry first sketched. A
  reviewed delete was wrong on both axes: `ALWAYS_REVIEW` governs an AGENT
  destroying SHARED content, and this is a person deleting their own private
  file. The reviewer could not read what they were approving, and the proposal
  row would itself announce that the file exists — the thing the 404-on-miss
  design spends effort preventing. It is a plain owner-scoped REST delete
  instead, the shape `services/chat_threads.py::delete_thread` already uses
  for a chat, which destroys strictly more. `GET /api/files` and the Settings
  card ship with it, because a quota with no list is a wall with no door.
  There is deliberately NO agent tool for deletion: absent beats reviewed.

  What stays open is retention, on purpose. A file lives until somebody
  deletes it, and it outlives the chat thread that carried it — the artifacts
  table has no link back to the thread, and cascading a file's death off a
  conversation's would destroy more than the person asked to destroy. Auto
  expiry is the other candidate and is not worth guessing a number for now:
  revisit when real usage shows quotas filling with files nobody would miss,
  which is also when "stale" can be defined from evidence rather than taste.
  In trusted-header mode a spoofed `X-User` can delete another person's
  files — the identical exposure chat deletion already carries, in the class
  the field-guide entry above records, closing the same way with api-key or
  oidc.

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
