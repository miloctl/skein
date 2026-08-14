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


The version bump in T4.2 is a trio: `backend/pyproject.toml`,
`frontend/package.json`, and `FALLBACK_CORE_VERSION` in
`app/extensions/contracts.py`. `tests/test_release_contract.py` fails if the
bump misses one.

## Phase 2 — Surface growth

Scoped by the spike, not by prediction. A second extension ("Meridian", a
fictional internal delivery system) was written outside this repository
against the installed wheel alone, composed, and exercised. It needed **zero
core changes** to sync work items, keep its own mapping table, run a
scheduled job, contribute policy and identity, and serve a governed tool and
specialist. Each item below is a wall it actually hit, in the order that
blocks a real integration.

- **T2.2 Public commands past task work.** `WorkItems` is `create_task`,
  `get_task`, `update_task`. Meridian's remote carries impediments, which are
  blockers in Skein's own vocabulary, so the sync filed them as tasks: the
  wrong entity, chosen because it was the only one on offer. Add blocker
  commands first as the template, then promises, each through its existing
  service with the same unforgeable context grant, idempotency receipt, and
  single write transaction. Never new SQL in the facade.
- **T2.3 Event catalog past task work.** Composition refuses
  `skein.blocker.created` with `event 'x' selects unknown event types`. The
  refusal is clean and fails closed, and it leaves an integration polling.
  An entity that gains a command gains its events in the same commit;
  envelopes stay content-free and a status change rides the `updated`
  event's change summary.
- **T2.6 Frontend page slot.** *(Unchanged, still conditional.)* Meridian
  deep-links to a core page anchor exactly as Atlas does, because that is the
  only option. Add the slot when a dashboard outgrows a card.
- **T2.7 Second extension ships.** Complete Meridian against the grown
  surface with the mandated test suite, including `assert_import_boundary`,
  which it already passes. Acceptance: zero core changes beyond the set
  above.

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

In slip order: T3.2, then T3.1, then T2.6 (the spike deep-linked to a core
page the way Atlas does, so a card still suffices), then T2.5.

Never cut: T2.2 and T2.3 for at least one entity, T2.4, T3.4, T4.1, T4.3.
T2.4 joins this list on spike evidence: without it an unattended integration
can be blocked but never queued, which is the most likely enterprise
requirement of the set.

## Risks

- **Review-shape unification corrupts pending reviews** (T3.1). Dual-read at
  verdict time. The test seeds an old-shape pending row from a running 0.2.1
  instance, never a hand-written facsimile.
- **Command scope creep** (T2.2, T2.3). The T2.1 gap list is the whole scope.
- **Page-slot forward compatibility** (T2.5). Core-range rejection must fire
  before the unknown-field path, proved on both host trees.
