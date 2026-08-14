# Release plan — v0.2.2

The work between `v0.2.1` and the second tagged release. Backlog and ideation
live in `docs/ROADMAP.md`; accepted debts live in `TODO.md`; this file holds
one release's committed work and drains as the work ships. A task that ships
leaves this file in the same commit, the way a shipped backlog item leaves
`docs/ROADMAP.md`.

Written 2026-08-13 from the extensibility assessment. The assessment scored
the extension boundary 8 modularity / 7 workplace-extensibility / 7
upgradeability-without-forking. This release targets 9 / 9 / 9. The tenth
point of upgradeability is production history across releases; no task can
buy it.

## Why 0.2.2 and not 0.3.0

Every extension declares `maximum_core_exclusive = "0.3.0"`. Staying inside
0.2.x keeps the existing Atlas artifact compatible, which makes the release's
own acceptance test the thing the assessment said was missing: an unchanged
private extension, built against 0.2.1, passing every contract on published
0.2.2. A 0.3.0 would invalidate that artifact and prove nothing about
upgrades.

Extension API stays `1.0`. Every contract change here is additive. A package
that uses a new contract declares `minimum_core = "0.2.2"`; a package that
does not keeps its existing floor and must keep working untouched.

## Invariants

Four rules govern every task below.

1. **The Atlas invariant.** The packed Atlas wheel and npm tarball built
   against 0.2.1 must pass, byte-identical, against 0.2.2.
   `scripts/reference-extension-contract.sh` and
   `scripts/reference-frontend-contract.sh` are the check. A task that breaks
   either has made a breaking change: rework the task, never the artifact.
2. **The admission rule** (`docs/ROADMAP.md`): a new contract slot ships with
   one core use and one private-package use. No empty slots.
3. **House rules.** Core migrations are new numbered files from `021_` up and
   never touch hash-chained `activity` rows. A new test must fail against the
   unfixed code. `./scripts/lint.sh` passes before every commit. New
   user-visible strings follow the wording rules in `CLAUDE.md` — a refusal
   is never warm.
4. **Every new contract lands inside the frozen-surface pin** in
   `tests/test_release_contract.py`, and every task appends its own line to
   `CHANGELOG.md` in its shipping commit. That pin and
   `tests/test_import_boundary.py` are what `scripts/hooks/pre-push` checks
   before a change reaches main. The slow artifact-level contracts stay on
   push-to-main for the reason recorded in `TODO.md`.

## Phase 1 — Integrity fixes

Small, independent, and freeze-sensitive: a release turns their absence into
compatible behavior.

- **T1.1 Close the extension route grant lifecycle.**
  `ExtensionRouteServicesDep` hands a route a `WorkItems` that nothing ever
  closes, so a thread the handler spawns can write core rows after the
  response — and after shutdown — under the route's provenance. Tools, jobs,
  and subscribers already revoke authority at their deadline; routes have no
  deadline, so the grant needs a request-scoped close. A late call returns
  `EXECUTION_CONTEXT_CLOSED`, matching the workflow-action behavior. Removes
  the matching entry from `docs/ROADMAP.md`.
- **T1.3 Separated review duties.** By default the human who prompted an
  agent can approve that agent's proposal. A workplace that needs separation
  must write a policy rule today. Add `SKEIN_REVIEW_SEPARATION=1`, which
  makes `services/review.py` refuse a verdict from the proposal's originating
  requester. Policy-named approver sets keep working and compose with it:
  both must pass.

The version bump in T4.2 is a trio: `backend/pyproject.toml`,
`frontend/package.json`, and `FALLBACK_CORE_VERSION` in
`app/extensions/contracts.py`. `tests/test_release_contract.py` fails if the
bump misses one.

## Phase 2 — Surface growth

The heart of the release. Sequenced: the spike names the scope, then the
scope gets built, then the spike's extension proves it.

- **T2.1 Second-extension spike.** Public commands added speculatively freeze
  shapes nobody validated. Author a second extension in a separate
  repository, clean-room, against the *installed* 0.2.1 artifacts, targeting
  a real workplace need (work-item sync with manager approval). Timebox it.
  Where it hits a wall, log the exact missing contract. That gap list — not
  this plan — fixes the scope of T2.2 through T2.5. Expected outcome:
  blocker and promise commands with their events, and a page-slot demand if
  the dashboard requirement is real.
- **T2.2 Public commands for blockers.** `WorkItems` exposes three task
  operations against a 63-file service layer, which is the largest single
  cause of future core changes. Extend the facade with blocker create and
  update, template-copied from the task implementation: the same unforgeable
  context grant, the same idempotency receipts, the same policy actions, the
  same single write transaction through `services/blockers.py`. Never new SQL
  in the facade.
- **T2.3 Public commands for promises.** The same shape, using T2.2 as the
  proven template, honoring the promise vocabulary (status, direction,
  audience). Engagements ship only if T2.1 demanded them.
- **T2.4 Event catalog growth.** Folded into the T2.2 and T2.3 commits: an
  entity that gains a command gains its events in the same commit. Envelopes
  stay content-free. Status transitions ride the `updated` event's change
  summary instead of minting a type per transition. Record the rule in
  `docs/EXTENSIONS.md`: a public command and its events ship together or not
  at all.
- **T2.5 Frontend page slot.** *(Conditional on T2.1 demand.)* The
  organization-dashboard scenario is the one scenario the assessment could
  not satisfy, and the Atlas reference already works around it with a
  deep-link to a core page anchor. Add an optional `pages` field to
  `FrontendExtension`, namespaced under `/ext/{extensionId}`, resolved by one
  core catch-all route against the frozen registry, gated by the same
  capability check as cards. The 0.2.1 host must reject a `pages`-using
  package by core range rather than crash on the unknown field; that
  forward-compatibility case belongs in the dual-host frontend contract.
- **T2.6 Second extension ships.** Complete the T2.1 extension against the
  grown surface, with the mandated test suite including the T0.2 check.
  Acceptance: **zero core changes beyond the planned T2.2–T2.5 set.** Any
  further core change it needs is a finding, not a patch.

## Phase 3 — Convergence and operations

Independent of Phase 2. T3.3 must land before the tag because it changes a
public contract shape.

- **T3.1 Stock tools onto the contribution harness, tranche 1.** Core tools
  bypass the `ToolContribution` harness: no schemas, no per-tool timeout, and
  a different review-proposal shape, which leaves two review disciplines to
  keep aligned. Migrate the four `SPECIALIZED_WRITE_TOOLS` first — they
  already run through `GovernedCoreTool` with review proposals. Pending
  old-shape reviews must stay approvable: dual-read at verdict time, the way
  migration 017's contract-version field already does it. Model-facing tool
  names must not change; persona allowlists and session history reference
  them. The remaining stock tools are later tranches, one per release.
- **T3.2 Notification delivery through the outbox.** *(Stretch. May slip to
  0.2.3.)* Channels are hardcoded to in-app rows and one Slack webhook, so a
  second channel is a core change today. Design first: outbox envelopes are
  content-free by contract and a delivery channel needs the body, so the
  likely shape is a notification event carrying source references and saved
  policy context, with the subscriber reading the body under its own service
  identity. Core's own Slack post becomes the first subscriber. Build only if
  the design holds; otherwise record the decision and defer.
- **T3.3 Extension-store backup opt-in.** `admin.backup()` copies the core
  and private databases only, and retention never touches an extension store,
  so every extension's data survives on deployment-side discipline. Add
  `include_in_backup` to `ExtensionStore`, defaulting to true, and copy
  declared stores beside the core databases. Retention stays extension-owned;
  say so plainly in `docs/EXTENSIONS.md`.
- **T3.4 Publishing channel.** No registry or publishing channel exists for
  any artifact, which is the root of the packaging workarounds the boundary
  audit counted. Use the Gitea instance's package registries for the wheel,
  the npm packages, both images, and the frontend host archive. Prove the
  pipeline by publishing the existing 0.2.1 artifacts first, so the tag
  reuses a working path.

## Phase 4 — The release

Strictly sequenced.

- **T4.1 Permanence sweep.** Whatever the tag ships becomes the compatibility
  floor. Confirm: no legacy-acceptance branch exists only for an unreleased
  state; `app.extensions.__all__` and `app.public.__all__` contain only
  documented, consumed symbols; every new contract is documented with its
  `minimum_core`; `tests/test_release_contract.py` pins the new surface; this
  file and `docs/ROADMAP.md` hold nothing this release shipped.
- **T4.2 Tag and publish.** Version bump in one commit —
  `backend/pyproject.toml`, `frontend/package.json`, and the T1.4 fallback,
  which the T1.4 test enforces as a trio. Finalize the CHANGELOG from its
  running section. Tag. Publish through T3.4.
  Note: the tag is also the moment core migrations stop being editable
  (`TODO.md`, single-replica entry).
- **T4.3 Upgrade rehearsal on the published pair.** Point the reference
  contracts at the published 0.2.1 and 0.2.2 artifacts instead of a
  `sed`-synthesized version identity. `scripts/upgrade-path.sh` finds two
  `v*` tags and stops skipping itself. **The release is done when** the
  unchanged Atlas artifacts pass every contract on published 0.2.2, the T2.6
  extension does the same, and migrations from `021_` up apply over a 0.2.1
  database with schema equality against a fresh build and an intact activity
  chain.

## Cut lines

In slip order: T3.2, then T3.1, then T2.5 (only if the spike proved cards
suffice), then T2.3.

Never cut: T0.2, T1.1, T1.4, T2.2 with T2.4 for at least one entity, T3.3,
T4.1, T4.3. Those are the tasks where the tag either locks in the fix or
locks in the flaw.

## Risks

- **Review-shape unification corrupts pending reviews** (T3.1). Dual-read at
  verdict time. The test seeds an old-shape pending row from a running 0.2.1
  instance, never a hand-written facsimile.
- **Command scope creep** (T2.2, T2.3). The T2.1 gap list is the whole scope.
- **Page-slot forward compatibility** (T2.5). Core-range rejection must fire
  before the unknown-field path, proved on both host trees.
- **The spike stalls the critical path** (T2.1). Timebox it. If it overruns,
  build the expected scope and let T2.6 validate afterwards — weaker
  evidence, same shapes.
