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

The second extension proved this phase and was then deleted: it needed no
core change, and its thirteen checks passed against the installed wheel.
Every wall it hit is now a contract, pinned by core tests that run in CI. A
future release that wants an independently authored extension again should
build one against a published wheel rather than resurrect that one.

The frontend page slot stays unbuilt. The reference deep-links to a core page
anchor the way Atlas does, and a card still carries what its dashboard needs.
The admission rule holds: add the slot when a real dashboard outgrows a card,
with one core use and one private-package use in the same change.

## Phase 3 — Convergence and operations

The publish pipeline is written and runs on a `v*` tag. It is unproven until
the Gitea instance has its package registry enabled and a `PACKAGE_TOKEN`
secret with `packages:write`. The two convergence items planned for this
release moved to `docs/ROADMAP.md`.

## Phase 4 — The release

Strictly sequenced.

- **T4.1 Permanence sweep.** Whatever the tag ships becomes the compatibility
  floor. Confirm: no legacy-acceptance branch exists only for an unreleased
  state; `app.extensions.__all__` and `app.public.__all__` contain only
  documented, consumed symbols; every new contract is documented with its
  `minimum_core`; `tests/test_release_contract.py` pins the new surface; this
  file and `docs/ROADMAP.md` hold nothing this release shipped.
- **T4.2 Tag and publish.** Version bump in one commit —
  `backend/pyproject.toml`, `frontend/package.json`, and
  `FALLBACK_CORE_VERSION`, which `tests/test_release_contract.py` enforces as
  a trio. Finalize the CHANGELOG from its
  running section. Tag. Publish through T3.4.
  Note: the tag is also the moment core migrations stop being editable
  (`TODO.md`, single-replica entry).
- **T4.3 Upgrade rehearsal on the published pair.** Point the reference
  contracts at the published 0.2.1 and 0.2.2 artifacts instead of a
  `sed`-synthesized version identity. `scripts/upgrade-path.sh` already runs
  against the `v0.2.1` baseline; a second tag makes it a real release pair.
  Atlas is the artifact for this hop: it keeps a `0.2.0` floor, uses none of
  the new commands, and must pass untouched. **The release is done when** the
  unchanged Atlas artifacts pass every contract on published 0.2.2, and
  migrations from `021_` up apply over a 0.2.1 database with schema equality
  against a fresh build and an intact activity chain.

## What is left

`T3.4` is the only remaining task that cannot be cut. Without a publishing
channel the release has nowhere to publish, and every consumer keeps building
from a source checkout. It needs the package registry enabled on the Gitea
instance and a `packages:write` secret, so it starts outside this repository.

`T4.1` through `T4.3` are the release itself, in order, and `T4.3` is the
definition of done.

`T3.1` and `T3.2` are the two this plan would drop first, and the
recommendation is to drop both from this release. `T3.1` buys internal
consistency rather than workplace capability and can ride along whenever a
stock tool is next touched for another reason. `T3.2` has a real design
tension to resolve first: an outbox envelope is content-free by contract and
a delivery channel needs the body.

## Risks

- **Review-shape unification corrupts pending reviews** (`T3.1`, if it is
  done at all). Dual-read at verdict time. The test seeds an old-shape
  pending row from a running 0.2.1 instance, never a hand-written facsimile.
- **The registry is proved by the release it is meant to carry** (`T3.4`).
  Publish the existing 0.2.1 artifacts first, so the tag reuses a path that
  already works instead of debugging a release and a pipeline at once.
