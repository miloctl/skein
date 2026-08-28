# Changelog

Step 1 of the upgrade procedure in `docs/EXTENSIONS.md` is to read the release
notes and deprecations. This file is those notes.

Each release records three kinds of change. **Contracts** covers anything a
private extension package imports or declares: `app.extensions`, `app.public`,
`@miloctl/skein-extension-api`, the content schemas, and the compatibility fields. A
package author reads this section and nothing else to plan an upgrade.
**Behavior** covers what the running system does differently. **Operations**
covers what whoever runs the server must do differently.

A contract entry names the version a package must declare to use it. Additive
contracts keep extension API 1.0: a package that does not use the new contract
keeps its existing `minimum_core` and needs no change.

## Unreleased

### Contracts

### Behavior

### Operations

## 0.3.2 — 2026-08-28

The next patch aligns the published frontend host with the tested workplace package boundary. Existing `0.3.x` extensions need no compatibility change.

### Contracts

- No `app.extensions`, `app.public`, or `@miloctl/skein-extension-api` signature changed. Extension API stays 1.0.
- Atlas stays at `2.0.0` with `skein-agents>=0.3.0,<0.4.0`. The next patch remains inside that range.

### Behavior

- Browse create controls now have stable form names. Browser autofill no longer reports empty form metadata for these controls.

### Operations

- Workplace tests use a hash-locked dependency closure constrained by the production lock. Current-package tests start in a fresh installed environment.
- The package-built browser contract uses signed OIDC identities for denied, integration, and manager paths. It also completes one core write.
- Contract cleanup is bounded and uses explicit disposable database names. Ambient Atlas credentials cannot enter the browser contract.
- Partial publication retries validate the original run and reuse its tested artifact. PyPI skips an identical wheel and refuses different bytes.
- Release preparation now uses one version input to update synchronized packages, exact artifacts, locks, documentation, and the release marker.
- A protected finalization workflow verifies registry bytes from the original artifact before it creates an annotated release tag.

## 0.3.0 — 2026-08-27

Two deployment defaults changed, and both are visible to a private package.
This is a MINOR release for exactly that reason: a package declaring
`skein>=0.2.0,<0.3.0` will not resolve 0.3.0, which is the cap doing its job.
Widen to `<0.4.0` and re-read the two Behavior entries below before you do.

### Contracts

- No `app.extensions`, `app.public` or `@miloctl/skein-extension-api` signature
  changed, and extension API stays 1.0. The only contract action is the
  compatibility range: a package that declared `maximum_core_exclusive =
  "0.3.0"` must declare `"0.4.0"` to load on this core. Nothing else in a
  package needs an edit for the version itself.
- The Python distribution is now `skein-agents` and publishes to public PyPI through GitHub OIDC. The public imports remain
  `app.extensions`, `app.public`, and `app.main.create_app`. Public PyPI owns
  the unrelated `skein` name, so current packages must not depend on it.
  The import boundary now scans Python stubs and permits only `create_app`
  from `app.main`. Bare, wildcard, private, and aliased private imports fail.
- The complete frontend host now publishes as the private GitHub package `@miloctl/skein-frontend-host`. A workplace project installs it with the private `@miloctl/skein-extension-api` package and runs
  `skein-frontend-build` after it compiles its private extension.
- Atlas 2.0 declares `skein-agents>=0.3.0,<0.4.0`. Atlas 1.x remains the
  pinned fixture for the explicit 0.2.3 package transition.

### Behavior

- **The review gate is ON by default.** `SKEIN_AGENT_REVIEW` defaults to 1;
  it defaulted to 0 through 0.2.x. A mutating agent write — including a
  governed extension tool at the `review` authority level — becomes a
  proposal a human approves, so `execute_tool` returns `review_required`
  carrying a `review_id` where it previously returned `completed` with the
  write already done. An extension whose job or test asserts `completed`
  must either assert both outcomes or grant that (agent, entity) pair
  `autonomous` in the authority matrix. `SKEIN_AGENT_REVIEW=0` restores the
  0.2.x behavior deployment-wide. This is the one trust boundary that
  shipped open while every other one failed closed, and it is what lets
  trust accrue at all: with the gate off no verdict is ever recorded, so no
  agent can earn autonomy.
- **The authentication mode fails closed.** `SKEIN_AUTH_MODE` defaults to
  `api-key`; it defaulted to `trusted-header` through 0.2.x. A deployment
  that set nothing authenticated every caller by a self-asserted `X-User`
  header and now refuses them. Set `SKEIN_AUTH_MODE=trusted-header`
  explicitly to keep the old posture, or supply real credentials.

- `fold_identity` now normalizes before it strips and composes after. The
  old order was not idempotent: a compatibility character that decomposes
  into whitespace ("¯" becomes space plus combining macron) kept the
  space, and a stripped zero-width joiner left a base and its mark
  uncomposed. Two spellings that render the same could fold to two
  identities. Property tests (`tests/test_identity_fold.py`) hold the fold
  to idempotence, case, compatibility forms, and invisible characters. A
  roster with names that only now fold equal surfaces them through the
  existing conflict quarantine, `python -m app.identity_audit`.

### Operations

- CI gates backend line coverage at 90% and prints frontend coverage.
  Local `pytest` is unchanged. `RELEASING.md` now records the release
  procedure. `./scripts/mutation-test.sh <module>` runs on-demand mutation
  testing over `app/services/`.
- The contract rehearsals pin their own `SKEIN_*` environment
  (`scripts/lib/hermetic-env.sh`) and take only `SKEIN_DATABASE_URL` from
  the caller, so a rehearsal answers the same way on a developer machine
  and in CI. Their steps are files under `scripts/contract/` rather than
  shell heredocs, so ruff checks them and a failure names a real file and
  line.
- `reference-images-contract.sh` builds and starts the core and Atlas images.
  It starts a PostgreSQL container on an isolated network and points both
  backend images at it. Skein requires PostgreSQL in 0.2.3 and later. The old
  gate supplied no server, so the image could not start. The contract does
  not use the caller's `SKEIN_DATABASE_URL`. That URL can point to a developer
  database, where image startup can run core migrations. Each readiness loop
  prints the container log when the service does not start.
- The Atlas reference repository owns one npm lock and one combined Python
  production lock. Its final images install exact package artifacts and do
  not inherit Skein application images.
- The old frontend-host archive and host-image contracts are removed. The
  clean frontend contract installs npm tarballs and starts the completed
  standalone server.
- A 0.2.x deployment must uninstall the old `skein` and Atlas 1.x
  distributions before it installs `skein-agents` and Atlas 2.0.

## 0.2.2 — 2026-08-13

The second tagged release, and the first that proves an upgrade: the Atlas
reference extension passes every contract unchanged across the 0.2.1 to 0.2.2
hop, and a database built by 0.2.1 reaches the same schema as a fresh 0.2.2
build with its activity chain intact.

### Contracts

- `WorkItems` gains blocker commands: `create_blocker`, `update_blocker`, and
  `get_blocker`, with `CreateBlockerCommand`, `UpdateBlockerCommand`, and
  `BlockerView`. An impediment from an external system is a blocker in
  Skein's vocabulary, and the facade previously offered only tasks, so an
  integration filed it as the wrong entity. An update resolves a blocker or
  corrects its wording; escalation stays with the scheduled sweep that owns
  the clock. Needs `minimum_core = "0.2.2"`.
- `WorkItems` gains promise commands: `create_promise`, `update_promise`, and
  `get_promise`, with `CreatePromiseCommand`, `UpdatePromiseCommand`, and
  `PromiseView`. A promise carries a direction, an audience, and a settlement
  status that no other entity has. It settles once. Needs
  `minimum_core = "0.2.2"`.
- The event catalog gains `skein.promise.created` and `skein.promise.updated`.
  Both need `minimum_core = "0.2.2"` in a subscribing package.
- The event catalog gains `skein.blocker.created` and `skein.blocker.updated`,
  emitted from the shared blocker write path so every caller produces them.
  Composition still refuses a subscription outside the catalog.
- A public work command that policy holds for review is now durable. A rule
  that returns `review` on any public work command stores the command, and `PublicError.review_id` names the proposal a human approves;
  approval runs the exact saved command under a new grant with the integration
  still recorded as its author. Before this a route answered `409` and a job
  answered `POLICY_REVIEW_UNSUPPORTED` and neither left anything to approve,
  so an unattended integration could be stopped but never asked. A rule on the
  operation action itself still returns `POLICY_REVIEW_UNSUPPORTED`: there is
  no request to resume. Needs `minimum_core = "0.2.2"` to read `review_id`.
- `ExtensionStore(path, include_in_backup=True)` puts an extension-owned
  database into the daily core backup. Previously nothing in core copied it,
  so every private package's data survived on deployment-side discipline
  alone. Skein does not mirror an extension store off the box, and retention
  stays extension-owned. Needs `minimum_core = "0.2.2"` only to set the flag;
  a store from an older package is backed up by the default.
- `app.extensions.assert_import_boundary` raises when a private package
  imports a Skein module outside `app.extensions`, `app.public`, and
  `app.main`. It reads source, so a dynamic import evades it: this is a drift
  check, not a security boundary. Use it for the import-boundary test that
  `docs/EXTENSIONS.md` requires. Needs `minimum_core = "0.2.2"`.

### Behavior

- A public read applies the caller's own visibility filter on every entity.
  `get_promise` read any row, so an extension route could return a teammate's
  private promise. Blocker and task reads were already filtered.
- An idempotency key names one kind of record. The receipt stored the kind and
  nothing compared it, so a key reused across two commands replayed one entity
  as another and returned a row the caller never wrote. A reused key now
  answers `IDEMPOTENCY_KEY_REUSED`.
- A linked write carries the project class of the row it attaches to. A
  blocker and a promise read that class from the caller instead, so a rule
  keyed on it governed task writes into a regulated engagement and skipped
  the other two.
- An approval releases the one action it answered. A command that meets two
  independent review rules is held again for the second, rather than riding
  the first approval through a gate whose approvers were never computed.
- The review queue carries a bounded preview of the command it is holding, so
  a status change bundled into a create is visible on the approve screen.
- A held command whose target vanished before the verdict settles as rejected
  instead of returning to the queue on every attempt.
- A domain write carries the declared effect and risk of the contribution
  performing it. A route, job, tool, event subscriber, or workflow action
  declares `effect` and `risk`, and the `work.task.*` decision previously
  reached the policy engine as `effect="none"`, `risk="low"` whatever the
  contribution said, so a workplace rule keyed on risk never fired on the
  write it meant to gate. Rules keyed on project type are unaffected.
- An extension route's work grant ends with its response. A route declares no
  deadline, so a thread the handler started kept writing core rows under the
  route's provenance after the response and after shutdown. A later call now
  returns `EXECUTION_CONTEXT_CLOSED`. Use a `JobContribution` for background
  work.
- `ExtensionStore` connections refuse `ATTACH`. The configured-path check
  sees only the file the store opened, so one `ATTACH` statement reached a
  core database from a connection that had already passed it. Both checks
  prevent accidents and neither is an isolation boundary.
- Composition logs a warning when no installed `skein` distribution names the
  core version and the source fallback is used instead. Every module
  compatibility range is checked against that number, so a guessed one can
  refuse a valid private package.

### Operations

- Core migration 021 widens the reviewed-invocation kinds so a held public
  command can be stored. It rebuilds `extension_review_invocations` and copies
  every existing row.
- `SKEIN_REVIEW_SEPARATION=1` refuses an approver who is the person a
  proposal came from, so an approval costs a second pair of eyes without a
  policy rule. Off by default. A policy rule that names `approver_groups`
  composes with it and both checks must pass. Rejection is unchanged: a rule
  that traps a proposal in the queue is worse than one person declining it.

## 0.2.1 — 2026-08-13

First release after the workplace extension boundary. Extension API 1.0 for
both the backend and the frontend.

### Contracts

- A tool contribution can declare `error_codes`, and Skein preserves a
  declared code from `PublicError` instead of returning the generic
  `tool_error`. Needs `minimum_core = "0.2.1"`.
- A reviewed tool that writes through the supplied `WorkItems` service runs
  its command in the reviewer's transaction. Needs `minimum_core = "0.2.1"`.
  A tool that performs no reviewed local write keeps a `0.2.0` floor.
- A workflow action that uses `WorkItems` after review has the same
  requirement. An external-only action keeps a `0.2.0` floor.
- `ExtensionStore.transaction` supplies an explicit SQLite transaction. Needs
  `minimum_core = "0.2.1"`. A package with a `0.2.0` floor uses `connect`.

### Behavior

- Core REST mutations, agent tools, classified MCP tools, workflow steps,
  contributed routes, scheduled jobs, and frontend capability checks all
  evaluate one composed policy engine. A workplace permit cannot remove a
  core denial.
- Core migrations 018 through 020 record durable identity ownership,
  notification sources, and creation-time policy context.

### Operations

- `SKEIN_MCP_MODULES` gives the standalone MCP process the same module
  composition the API process uses. Without it that process composes core
  only, which leaves two policy boundaries in one deployment.
- `python -m app.identity_audit` reports and repairs roster identity
  conflicts. Run the documented claim commands before the first restart on
  this release.
