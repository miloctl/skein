# EXTENSION-BOUNDARY-AUDIT.md

Independent adversarial audit of `feature/workplace-extensibility`:
real extension boundary, or elaborate plugin framework?

Audit date: 2026-08-12. The audit itself was read-only: it examined the
branch as of head `5afa984` and modified nothing. This file is untracked.

**Post-audit remediation (same day, commits `243cdc5..db1903c`).** After
this report, the pre-merge simplification it recommended (§25 items 1–4, 7)
was implemented on the branch in seven commits: the 1.0 export surface was
trimmed (workflow engine and plumbing symbols removed, lifecycle and
`requires` slots deferred, constant and unread fields dropped); the
pre-release compatibility shims were deleted; the test-invocation surfaces
(`registry_for`, `execute_tool`, `dispatch_events`) were published and the
Atlas reference gained a 10-test suite that the cross-version rehearsal now
runs against both installed cores; event subscriptions are validated
against a published catalog at composition (closing the silent-delivery
trap); the policy-engine fallback now fails closed; the example frontend's
monorepo `file:` dependency was removed and the frontend contract performs
real npm resolution of the packed package; and the authoring documentation
gaps (env vars, review endpoints, wheel build, approval model, store
trust wording, contract reference) were closed. The scores and findings
below describe the branch as audited, before those commits.

## 1. Executive verdict

**Verdict B — Real and usable, but broader than necessary** (and, on the
one axis that matters most for adoption, *narrower* than the domain needs).

The boundary is real. A clean-room extension ("Northstar Workplace
Extension"), written outside the repository from the published
documentation and installed artifacts only, exercised all thirteen mandated
capabilities — namespaced route, scheduled job, integration adapter with a
fake remote, custom policy rule, governed read tool, review-gated write
tool, specialist, versioned event subscriber, extension-owned data store
with its own migration stream, versioned content overlay, frontend
navigation entry, dashboard card, and capability-aware hiding — with
**zero core-source modifications, zero internal imports in shipped code,
and zero direct core-database access**. Forged command contexts, policy
escalation attempts, duplicate identifiers, and incompatible version ranges
were all rejected with clear errors. Core itself runs on the same
composition mechanisms (routes, jobs, policy engine, workflow actions), so
the seam is load-bearing, not decorative. Zero reference-extension
special-casing exists in production core.

What keeps it out of Verdict A:

1. **The mandated test suite cannot be written externally.** The authoring
   doc requires tool-gating, timeout, receipt, and event-retry tests, but
   the only invocation surfaces (`app.extensions.tools.execute_tool`,
   `app.public.events.dispatch_events`, `app.state.skein_registry`) are
   un-exported internals. The reference Atlas extension ships no tests, so
   no external testing pattern is demonstrated anywhere.
2. **Documentation gaps force artifact inspection.** Sixteen gaps and two
   contradictions were logged in the documentation-only stage: no field
   reference for any contribution dataclass, undocumented event type names,
   undocumented operational env vars (`SKEIN_DATA_DIR`, `SKEIN_SCHEDULER`,
   auth modes), an undocumented review-approval endpoint, import-time
   config binding, and a wheel default data dir inside site-packages.
3. **The public surface is padded at the symbol level.** Roughly 16 of ~64
   exported symbols have no consumer outside core internals and synthetic
   tests: a workflow engine whose only sanctioned use is not using it, a
   lifecycle slot no extension consumes, dependency-ordering machinery with
   no consumer, constant-valued manifest fields, three version fields that
   gate nothing, and pre-release backward-compatibility shims for packages
   that cannot exist.
4. **Coverage is task-first.** `WorkItems` exposes 3 operations against a
   63-service-file domain; the outbox carries 2 event types. The first
   ambitious workplace extension will still need a core contribution — but
   it arrives at a contract to extend, not a fork to maintain.
5. **No real release pair exists.** The upgrade rehearsal is genuine (two
   source trees 17k lines apart, byte-identical private artifact across the
   move), but the "0.2.1" identity is synthesized by `sed` on the working
   tree, the repository has zero release tags, and `upgrade-path.sh`
   self-skips everywhere.

**Merge recommendation: merge after targeted simplification.** The required
work is small relative to the branch: trim the dead public symbols before
merge freezes the 1.0 API line, export or document a test-invocation
surface, close the documentation gaps, fix the reference extension's
monorepo `file:` dependency, and correct one false doc claim. Details in
§20–22 and §25.

## 2. Base and feature commits

| Item | Value |
|---|---|
| Base branch | `main` = `d3b0f2ebbb6437b9ba34afb398d548ec955d3ae3` |
| Feature branch | `feature/workplace-extensibility` = `5afa9841916b8838f4971e045575ad257889a8ab` |
| Merge base | `d3b0f2e` (branch is a clean descendant of main) |
| Commits | 82 (~50 are `fix:` remediations from ≥8 internal review rounds) |
| Diff | 211 files, +39,660 / −1,351 |
| Core version | 0.2.0 (`backend/pyproject.toml`) |
| Extension API | backend 1.0, frontend 1.0 |

Major additions: `backend/app/extensions/` (2,910 LOC, 8 modules),
`backend/app/public/` (2,087 LOC, 5 modules),
`backend/app/services/policy_context.py` (878 LOC),
`frontend/packages/extension-api/` + `frontend/lib/extensions/` +
`frontend/scripts/compose-extensions.mjs` (~430 LOC),
`examples/workplace-extension/` (Atlas reference, 1,716 LOC total / 891
source), core migrations 012–020, four contract-rehearsal scripts +
`package-frontend-host.sh` + `upgrade-path.sh` (~1,100 LOC), ~12,600 LOC of
extension-focused tests, and ~6,700 LOC of docs (`docs/EXTENSIONS.md`,
`docs/WORKPLACE-EXTENSIBILITY.md`, `WORKPLACE-EXTENSIBILITY-RESULTS.md`,
`docs/exec-plans/workplace-extensibility.md`).

## 3. Audit scope

Read: `docs/WORKPLACE-EXTENSIBILITY.md` (the historical assessment, treated
as context, not proof), `docs/exec-plans/workplace-extensibility.md`,
`WORKPLACE-EXTENSIBILITY-RESULTS.md` (the branch's self-reported results),
`docs/EXTENSIONS.md` (the extension-author documentation), packaging,
deployment, test, and CI configuration. No `AGENTS.md` files exist in the
repository; `CLAUDE.md` was read in their place.

Five independent review agents ran without sharing preliminary conclusions:
R1 clean-room extension author, R2 minimality/API-surface, R3
architecture/fork-risk, R4 security/lifecycle, R5 packaging/upgrade. The
main audit established the repository facts, cross-checked reviewer
evidence, and synthesized this report. Tests executed across the audit:
23/23 clean-room tests against installed artifacts, 278 extension-security
tests, 162 contract-suite tests, 88 composition tests, plus the backend and
frontend contract-rehearsal scripts (both exit 0).

## 4. Clean-room methodology

R1 built the fictional **Northstar Workplace Extension**
(`northstar.workplace`) in a temp directory entirely outside the
repository. Stage 3A used only `docs/EXTENSIONS.md`, public package
metadata, and exported public types; the Atlas reference source and the
contract scripts were not read until Stage 3A gap-logging was complete. The
core wheel was built from a `git archive` copy (the same process
`scripts/reference-extension-contract.sh` uses) and installed with the
Northstar wheel into a fresh venv with no source tree on `PYTHONPATH`.
First working capability: ~7 minutes after the doc read; full vertical
slice including the composed frontend production build: ~23 minutes.
Artifacts retained at
`/tmp/claude-1000/-home-milo-gitea-skein/63f13091-c410-49e9-901a-be8045e70a81/scratchpad/r1-cleanroom/`
(includes `3A-doc-gaps.md`, both wheels, the composed frontend host with a
completed `next build`, and the 23/23 passing test evidence). Security
probes are retained under `…/scratchpad/r4-security/`.

## 5. Public extension-surface inventory

- **Public packages**: `app.extensions` (42 exported symbols), `app.public`
  (12), `@skein/extension-api` (9), plus `app.main.create_app` →
  **~64 public symbols**.
- **Contribution slots: 14** — backend 12 (routes, jobs, lifecycle,
  policies, identities, service_identities, contexts, tools, specialists,
  events, migrations, workflow_actions) + frontend 2 (navigation,
  dashboardCards).
- **Execution-context types: 8**, five structurally identical (subject,
  policy, `WorkItems`, namespace, correlation id) — deliberately separated
  per runtime; none is a service locator.
- **Registries: 2** (backend `ExtensionRegistry`, frontend
  `lib/extensions/registry.ts`). **Lifecycle hooks: 1 slot**
  (startup/shutdown pair), consumed by no extension anywhere.
- **Manifest schemas: 3** — `SkeinModule` (5 required compatibility fields
  + 12 slot tuples, all validated at composition), `FrontendExtension`
  (5 required + 2 slots, validated at registry build), and `extension.toml`
  (**not runtime-load-bearing**: read only by core-repo tests and release
  scripts).
- **Required compatibility fields: 5 per manifest** (`module_id`/`id`,
  `version`, `extension_api`, `minimum_core`, `maximum_core_exclusive`) —
  all enforced (backend `registry.py:443-455`, frontend `registry.ts:43-50`).
- **Extension-specific configuration concepts**: private composition root
  calling `create_app(modules=…)`, `SKEIN_FRONTEND_EXTENSIONS` build-time
  allowlist, versioned frontend host artifact or Dockerfile `host` stage,
  deployment overlays — plus the **undocumented** operational set
  (`SKEIN_DATA_DIR`, `SKEIN_SCHEDULER`, auth-mode vars, import-time env
  binding).
- **Smallest working extension** (one route + one dashboard card): ~7 files.
- **Concepts before the first route/card: ~20**, including three
  policy-action namespaces evaluated on one write (`skein.rest.*`, the
  declared domain action, `work.task.*`) — the doc explains two of the
  three well.
- **Execution layers, registration → served write: ~9–10**, with 3
  independent policy evaluations; each traced layer has an articulable
  responsibility (§10).

Evidence table (condensed; per-symbol detail in the R2 review):

| Public concept | Core uses | Atlas uses | Clean-room uses | Tested | Removal candidate |
|---|---|---|---|---|---|
| SkeinModule / create_app / Route- / Job- / Policy- / Identity- / ServiceIdentity- / Tool- / Specialist- / Context- / Event- / Migration- (+ExtensionStore) / WorkflowAction-Contribution | Yes | Yes | Yes (12 of 13 backend slots) | Yes | No |
| WorkItems, CommandContext, Create/UpdateTaskCommand, TaskView, PublicError | Yes | Yes | Yes | Yes (74 tests) | No |
| Frontend Navigation / DashboardCard / Card / EmptyState | Yes (core `Card` re-exports from the package) | Yes | Yes | Yes | No |
| LifecycleContribution | Loop exists; core registers zero hooks | **No** | Failure-isolation test only | Synthetic only | **Defer** |
| SkeinModule.requires + topo-sort + cycle detection | No | No | No | Synthetic only | **Defer** |
| WorkflowEngine / WorkflowContext / WorkflowResult in `app.public.__all__` | Internal | typecheck only | No | Forgery-refusal only | **Remove from public** |
| PolicyAPIRoute, PolicySubjectDep, decide, enforce_decision, subject_for, approval_fingerprint, policy_input_data, policy_input_from_data | Yes (core plumbing) | **No** | No | Not as extension API; 0 mentions in EXTENSIONS.md | **Remove from `__all__` or document** |
| ToolContribution.receipt / .provenance | Validator accepts exactly one value each | Defaults | Defaults | Rejection test only | **Remove fields** |
| ContextContribution.version, SpecialistContribution.version, EventContribution.version | Validated, never read | Set | Set | No consuming semantics | Bind or drop |
| AppSettings (carries `api_token`) | Factory parameter | typecheck only | No | Yes | Narrow/document |

## 6. Clean-room extension structure

Backend: 10 shipped source files, **513 LOC** (`src/northstar/`), plus 38
LOC packaging/content and 720 LOC tests/composition. Frontend: 3 files,
**112 LOC**. Total ≈ **1,345 LOC** — comparable to Atlas (1,716 total, 891
source). All 13 required capabilities and all mandated test categories are
present; 23/23 tests pass against installed wheels; strict mypy is clean
against the PEP 561 core wheel.

## 7. Documentation-only attempt results

16 gaps + 2 contradictions (full log: R1's `3A-doc-gaps.md`). Highest
impact:

1. No API/field reference for any contribution dataclass — constructors are
   discoverable only from source or the wheel.
2. Event type names (`skein.task.created`/`.updated`) appear in no document
   or export; discovered by reading outbox rows after a silent failure.
3. No documented way to trigger outbox delivery or to invoke a governed
   tool or job in tests — yet the "Required tests" section mandates exactly
   those tests.
4. Operational env vars undocumented; config binds at import time; the
   installed-wheel default DB path resolves inside site-packages.
5. Review approval endpoint (`POST /api/review/{id}/approve`) undocumented;
   the playbook YAML schema is published only as stock content inside the
   wheel; the wheel build command itself is never given.

## 8. Packaging-isolation results

- Core wheel (644 KB): `py.typed` present; all 20 migration SQL files
  inside; personas/flocks/playbooks/fieldguide ship as wheel data resolved
  via `sysconfig` — **nothing requires the source tree**. `create_app()`
  from the wheel alone boots with the mock provider (`/health` 200).
- `@skein/extension-api` tarball: exactly 3 files (`index.js`, `index.d.ts`,
  `package.json`); its only import is `react` — fully self-contained, no
  `../../lib` reach-back.
- The frontend host archive works: `npm ci` inside it succeeds and a
  production build with `SKEIN_FRONTEND_EXTENSIONS=@northstar/skein-extension`
  statically imports the private package through the generated registry.
- **Workarounds: 8** (R5's count). Significant: `sed`-synthesized 0.2.1
  version identity; hand-assembled `node_modules` (tar-extract + monorepo
  symlinks for react/@types) instead of real npm resolution;
  `--legacy-peer-deps` in the derivative Frontend.Dockerfile; Atlas's
  `file:../../../frontend/packages/extension-api` devDependency, which
  fails `npm install` outside the monorepo; no registry or publishing
  channel for any artifact.

## 9. Core-modification results

Verbatim, captured after the complete clean-room experiment:

```
=== git status --porcelain ===   (empty, exit 0)
=== git diff ===                 (empty, exit 0)
On branch feature/workplace-extensibility
nothing to commit, working tree clean
```

Forbidden-import grep of shipped Northstar source: **none**. Shipped code
imports only `app.extensions`, `app.public`, `app.main.create_app`,
`@skein/extension-api`, and `react`. The Northstar **tests** required 3
internal imports (`app.extensions.tools.execute_tool`/`ToolCallContext`,
`app.public.events.dispatch_events`, `app.state.skein_registry`) — the
basis of blocker-adjacent finding H1 (§18). Core files changed to add the
clean-room extension: **zero**. Hard-coded registry edits: **zero**.
Workplace conditionals in core: **zero**.

## 10. Execution-path traces

- **Private route write (~10 layers)**: registry validation → FastAPI
  resolution → perimeter auth → identity mapping → declared
  operation-contract policy (`contributed_route_policy`) → bound
  `ExtensionRouteServices` grant → identity-keyed context issue →
  `WorkItems.create_task` in one `BEGIN IMMEDIATE` (visibility resolution →
  `work.task.create` policy → idempotency receipt) → core
  `services.work.create_task` (provenance + activity + outbox event, same
  transaction) → `TaskView` response.
- **Governed write tool through review (~12 layers)**: contribution
  validation → agent allowlist → capability check → input-schema validation
  → resource resolver → policy → `approval_fingerprint`
  (input+decision+contract) → durable proposal in the core review DB → REST
  approve with approver-capability check → fingerprint recheck
  (`approval_stale` on drift) → bounded worker with bound
  `ToolHandlerContext` → `WorkItems` write (policy again, idempotency,
  provenance `origin extension:…`, actor = agent) → correlated receipt.
- **Event delivery (~9 layers)**: outbox row in the writer's transaction →
  dispatcher job → type/version/visibility match → delivery dedupe →
  service-subject resolution → per-event policy (review = terminal refusal)
  → bound context on a timeout executor → delivery receipt with retry/dead
  accounting.
- **Extension migration (~5 layers)**: contribution validation → lifespan
  `store.migrate` → core-DB-path refusal guard → per-migration
  `BEGIN IMMEDIATE` with name+digest immutability → version row.
- **Frontend card render**: build-time static import via generated registry
  → manifest validation (API + core range + namespaces) → capability fetch
  (`/api/capabilities`, same policy engine, deny-until-decided) →
  conditional render in the `manager-dashboard` slot.

Global state encountered: `app.state.skein_registry`; a contextvar policy
engine with a core-rules-only `_DEFAULT_ENGINE` fallback
(`extensions/policy.py:401-405`) — fail-open for *workplace* rules if a
future entry point forgets `set_policy_engine`; process-global weak-ref
identity registries in `public/work.py` (the enforcement mechanism itself —
probed sound); import-time config binding. Duplicated metadata: route
method+path written twice (decorator vs `RouteOperationContribution` —
validated, so drift fails fast at startup); namespaces re-typed in every
contribution name; core version range duplicated across backend module and
frontend manifest (by design). The one removable-looking layer (triple
policy evaluation) is defensible as REST-perimeter + declared-operation +
domain-write, but only two of the three namespaces are documented well.

## 11. Core-adoption findings

Core substantially lives on the boundary — the strongest evidence is
subtractive, not additive:

- All 6 core routers are `RouteContribution`s in the `skein.core` module
  (`extensions/core.py:34-41`; `main.py:903-910`).
- All 18 core jobs are `JobContribution`s in the same scheduler loop behind
  the same policy gate (`main.py:56-79, 186-206`).
- The pre-existing authority matrix was **moved inside** the policy engine
  as its terminal `CorePolicy` rule (`extensions/policy.py:99-140`); core
  REST mutations flow through `PolicyAPIRoute`/`enforce_mutation_policy`
  (`routes/api.py:69`, `extensions/fastapi.py:379-388`), and ~35 core read
  sites row-filter through the composed engine.
- Core playbooks execute contributed workflow actions
  (`routes/api.py:3038`) — load-bearing, not parallel.
- Core's own `Card`/`EmptyState` are re-exports **from**
  `@skein/extension-api` (`frontend/components/card.tsx` is 3 lines).

Asymmetries: core tools are policy-gated by the same engine
(`tools/_gate.py:204`) but bypass the `ToolContribution` harness (no
pydantic I/O schemas, no per-tool timeout, a different review-proposal
shape — `services/review.py:117` vs `:254`); core jobs skip the window
claim and thread-pool timeout; `skein.core.agent-run` is special-cased
(with an honest comment) to receive the trusted registry; core frontend
nav/cards are static JSX while the registry carries extension items only.

**Reference special-casing: clean.** `grep -rni atlas` over production
core: 7 hits, all pre-existing on main (a fictional demo-project name in
comments). Zero reference identifiers, route prefixes, or package names in
core. The only monorepo-only behavior is test-only: `sys.path.insert` in
`backend/tests/test_reference_workplace_extension.py:15-18`; the Atlas
package itself depends on `skein>=0.2.0,<0.3.0` and imports only the
declared boundary.

## 12. Security and encapsulation findings

278 extension-security tests pass (26.9s). Probe results (R4; scripts
retained):

- **Command-authority minting: enforced.** Forged `CommandContext` →
  `COMMAND_CONTEXT_REQUIRED`; post-issuance field mutation → signature
  mismatch; id-reuse after GC → refused (`public/work.py:74-110, 240-249`).
- **Policy monotonicity: enforced.** DENY(2) > REVIEW(1) > PERMIT(0)
  combined by `max`; a workplace rule can escalate but can never lower a
  core deny. Identity refresh never raises authentication strength.
- **Tool governance: enforced.** Unknown effects fail closed; undeclared
  error codes mask to `tool_error`; timeouts → `completion_unknown`;
  verdict-time resource re-resolution and fingerprint staleness
  (`approval_stale`); receipts and provenance forced by validation.
- **Events: enforced.** The envelope carries field *names*, never body
  text; subscriber policy runs pre-handler; delivery is idempotent by
  `(event_id, subscriber)`; retries are bounded → dead-letter.
- **Identity: enforced.** Transactional, case-folded (NFKC + casefold)
  name reservation; `anonymous` and core actors unclaimable; service
  subjects collision-checked against humans, personas, and flocks.
- **Collisions: enforced** at build time for contribution names, model-tool
  names, service subjects, and `(method,path)` including the core
  namespace; extensions confined under `/api/extensions/{module_id}`.
- **Frontend hiding is presentation-only** — `/api/capabilities` derives
  from the same engine and says so; the backend enforces independently.
- **Failure isolation: fail-closed.** A raising startup handler rolls back
  started modules and prevents app start (deliberate; only *shutdown*
  tolerance is documented — a gap). One faulty job cannot block the
  scheduler loop; a throwing subscriber is bounded to dead-letter.
- **Review integrity: enforced.** Resource and policy re-checked at verdict
  time; content digests bind playbook approvals; legacy contract-version-0
  rows fail closed; reviewers must be human and hold any policy-named
  approver groups/capabilities.

Gaps — all Low within the documented trust model, which
`docs/EXTENSIONS.md:785-786` states honestly ("In-process modules are
trusted code with the same operating-system permissions as Skein"):

- **L1**: `ExtensionStore`'s core-path refusal is a path-string check —
  bypassed in probes via hardlink and `ATTACH DATABASE`. The doc sentence
  "The store refuses both core database paths" reads as a guarantee it is
  not. Soften the wording; optionally deny `SQLITE_ATTACH` via an
  authorizer.
- **L2**: self-declared `effect`/`risk` is advisory; a mis-declared `read`
  skips effect-scoped workplace rules (the domain write re-authorizes, so
  no escalation beyond the caller).
- **L3**: task `title`/`description` reach policy-rule code and the durable
  review store; document this exposure.
- **L4**: no default four-eyes — the human who prompted an agent can
  approve its proposal unless a workplace policy names approver groups.
- **I1**: `MigrationContribution.store` is a Protocol, not necessarily an
  `ExtensionStore` — a hostile store could target core tables (inside the
  trust model; note beside L1).

Core migrations 012–020 respect the append-only and activity hash-chain
rules (no UPDATE/DELETE of seq-carrying rows).

## 13. Minimality and bloat findings

At the **slot level**, "narrow by design" largely survives attack: 12 of 14
contribution types have demonstrated Atlas consumers, core dogfoods
routes/jobs/policy/workflow through the same contracts, the frontend was
held to two slots, and the refused-complexity list (no entry-point
scanning, no universal plugin base, no EAV store, no runtime browser
modules) is real and verified absent.

At the **symbol level** it does not survive: ≈16 of 64 exported symbols
have no consumer outside core internals and synthetic tests (F1–F7 in §20–21);
one slot fails the doc's own admission rule ("Add a core slot only when a
real workplace extension needs it"); and the branch ships
backward-compatibility layers for prior states of itself (legacy resolver
inference, digest backfills, untagged-digest acceptance) that become
permanent the moment 1.0 is released.

Weakest complexity ROI: the 853-line workflow engine (HMAC-style grants,
owner-thread dispatcher, review resume) whose demonstrated demand is one
Atlas action; the mid-workflow-approval scenario is the only thing
routes+policy cannot already do. Specialists-as-code duplicates a concern
the persona/flock content machinery already handled declaratively. The
API-to-consumer line ratio is ≈6:1, though most enforcement lines are
security invariants any correct implementation would need — the padding is
in the export list and manifest fields, not the enforcement machinery.

## 14. Upgradeability findings

What was actually executed (R5): `reference-extension-contract.sh` exit 0
in 38s — clean-venv installs from wheels, old-core (0.1.0) rejection with
the exact `ExtensionValidationError`, installed startup on both cores, a
real Atlas sync on both, migrations 018–020 applied by `db.init_db()`,
identity-audit claim/rename flows including a genuine collision quarantine,
pending reviews created on 0.2.0 and approved on 0.2.1, strict mypy against
the installed wheel, fresh-vs-upgraded schema equality.
`reference-frontend-contract.sh` exit 0 in 53s — the same packed Atlas
tarball built on two pinned host trees (`952ff3a` vs HEAD, tree-hash
drift-guarded), byte-compared compiled output, bare-Node consumer check.

The 0.2.0→0.2.1 mechanism, precisely: core 0.2.0 = `git archive d611d79`
(an ancestor on this branch); core "0.2.1" = tar-copy of the working tree
with `sed` on the version string; a guard asserts the two backend trees
differ (they differ by 17,226 insertions across 100 files). The private
Atlas wheel is installed once and never reinstalled across the core swap —
byte-identical by construction. So the rehearsal is genuinely
**two distinct source trees**, but a synthetic **version identity**: no
commit, tag, or release ever carried 0.2.1; the repo has zero release tags;
`upgrade-path.sh` self-skips ("no v* release tag") everywhere, including
CI. CI runs all four contract scripts but only on push to main — this
branch's contracts have never run in CI.

Compatibility metadata is enforced at both ends (backend
`registry.py:443-455`, frontend `registry.ts:43-50`) with tests, and
`test_release_contract.py` pins the version triple and the frozen API-1.0
import surface. Deprecation policy is documented (one released
compatibility range). Extension migrations are isolated in a separate
SQLite file with a namespaced, digest-checked stream; core migrations do
not touch private tables.

**Required judgment: upgradeability is designed and contract-tested, but
not empirically demonstrated across two compatible releases.** This caps
the dimension at 8; the conservative score lands at 7 (§17).

## 15. Comparison with simpler alternatives

| Capability | Implemented mechanism | Simplest credible alternative | Justified or excessive |
|---|---|---|---|
| Routes | In-process namespaced routers + per-operation policy contract | External service on the REST API | **Justified** — shared identity/policy/provenance cannot be replicated externally; triple policy evaluation is heavy |
| Jobs | `JobContribution` + window claims + service identities + timeouts | External cron + REST client | **Borderline** — Atlas's sync would work as cron+REST; justified only by policy and single-flight integration |
| Policy | Narrow-only combining engine, scoped rules | None credible (config cannot express org rules) | **Justified** — the feature's core value; deny>review>permit is well designed |
| Identity mapping | Callable mapper + resolver callbacks + `resolves_groups` tri-state | Declarative group→role config table | **Partially excessive** — Atlas's mapper is a 5-line groups check; only verdict-time refresh genuinely needs code |
| Governed tools | `ToolContribution` + typed schemas + review resume + resource resolver | MCP server (already supported, with governed metadata) | **Partially justified** — overlaps MCP; the differentiator (target-project classification) is real but narrow |
| Specialists | Code contribution of pure data | **Content overlay** — personas/flocks already exist | **Excessive as a code slot** — a persona schema referencing registered tools would have covered it |
| Events | Durable outbox + retry budgets + dead-letter + visibility tiers | Polling the activity feed / webhooks | **Justified** — the correct reliable-integration pattern; but only 2 event types exist |
| Extension data | `ExtensionStore` (142 LOC): path guard + digest-checked migrations | Extension opens its own SQLite (it is trusted code) | **Justified as a guardrail** — thin, cheap, optional |
| Workflow actions | 853-line 4-step engine + grants + owner-thread dispatcher | Approval-gated playbooks (policy on `playbook.create`) + extension routes | **Weakest ROI** — one consumer; the largest single complexity item |
| Content overlays | `schema_version: 1` strict schema | Already configuration | **Justified** — cheap tightening |
| Frontend nav/cards | Build-time static composition, 2 slots, policy-gated visibility | Link-out / separate app | **Justified and genuinely minimal** — the most disciplined part of the branch |

Correctly kept as configuration/content/external: static templates,
prompts, flock groups, environment values, secrets, untrusted integrations
(sidecars). Should remain core contributions: new public commands on
unsupported entities, new frontend slots (the doc says so itself).

## 16. Reviewer reports and scores

Full reports are in the session transcripts; scratch evidence under
`…/scratchpad/r1-cleanroom/` and `…/scratchpad/r4-security/`.

| Dimension | R1 clean-room | R2 minimality | R3 architecture | R4 security | R5 packaging |
|---|---:|---:|---:|---:|---:|
| Boundary reality | 9 | 8 | 8 | 7 | 9 |
| Boundary minimality | 7 | 6 | 8 | 9 | 8 |
| Extension-author usability | 6 | 6 | 7 | 7 | 7 |
| Encapsulation and safety | 9 | 9 | 6 | 9 | 9 |
| Upgradeability without forking | 7 | 8 | 6 | 8 | 8 |
| Complexity ROI | 6 | 5 | 6 | 6 | 7 |
| Confidence | high | med-high | high | med-high | high |

Reviewer verdict sentences: R1 "the boundary is real from outside the repo…
but the documentation and test-invocation surfaces are not external-grade";
R2 "architecturally disciplined, symbolically padded"; R3 "a real
composition seam, and core substantially lives on it — not decorative";
R4 "pass, pre-merge, within the documented trust boundary"; R5
"separate-repo viability: yes, demonstrated; upgradeability
designed and contract-tested, not demonstrated across releases".

## 17. Aggregated conservative scores

Conservative = lower of the main-audit score and the reviewer median,
further capped where a proven blocker contradicts it (none did; the
upgradeability cap is already reflected).

| Dimension | Main audit | Reviewer median | Reviewer minimum | Conservative score |
|---|---:|---:|---:|---:|
| Boundary reality | 8 | 8 | 7 | **8** |
| Boundary minimality | 7 | 8 | 6 | **7** |
| Extension-author usability | 6 | 7 | 6 | **6** |
| Encapsulation and safety | 8 | 9 | 6 | **8** |
| Upgradeability without forking | 7 | 8 | 6 | **7** |
| Complexity return on investment | 6 | 6 | 5 | **6** |

Diagnostic counts (evidence, not optimization targets):

| Count | Value |
|---|---|
| Extension-specific production lines added (boundary machinery) | ≈6,300 (extensions 2,910 + public 2,087 + policy_context 878 + frontend ~430); total branch +39,660 |
| Public extension API lines | ≈5,400 |
| Reference extension lines | 1,716 (891 source) |
| Clean-room extension lines | ≈1,345 (513 backend src + 112 frontend + tests/packaging) |
| Public concepts | ~64 symbols, 14 slots, 8 context types, 3 manifests |
| Concepts used by no external extension | ≈16 symbols + lifecycle slot + `requires` |
| Internal imports required by the clean-room extension | 0 shipped / 3 in tests |
| Core files changed to add the clean-room extension | 0 |
| Packaging or deployment workarounds | 8 |
| Unresolved high-risk escape hatches | 0 outside the documented trust model; 1 practical inside it (ExtensionStore ATTACH/hardlink, L1) |

## 18. Blocker and severity-ranked findings

**Blockers: none** — every mandated audit stage completed.

**HIGH**

- H1. The mandated extension test suite cannot be written without internal
  imports (`app.extensions.tools.execute_tool`,
  `app.public.events.dispatch_events`, `app.state.skein_registry`); Atlas
  ships zero tests, so no external testing pattern exists. (R1)
- H2. Public command surface is task-only: `WorkItems` = 3 operations vs a
  63-service-file domain; outbox = 2 event types. Honestly documented, but
  it guarantees near-term core PRs for any ambitious workplace. (R3)
- H3. Dead-at-birth public API frozen into 1.0: `WorkflowEngine`/
  `WorkflowContext`/`WorkflowResult` in `app.public.__all__` (doc forbids
  their use); 8 undocumented core-plumbing symbols in
  `app.extensions.__all__`; the doc's claim "the Atlas example uses every
  supported contract" is false for `lifecycle`, and the coverage test is
  shaped so it does not notice. (R2)
- H4. No real release pair: zero `v*` tags; "0.2.1" is sed-synthesized;
  `upgrade-path.sh` self-skips; contract scripts run in CI only on push to
  main, so this branch's contracts have never run in CI. (R5)

**MEDIUM**

- M1. Operational configuration undocumented for external authors
  (`SKEIN_DATA_DIR`, `SKEIN_SCHEDULER`, auth modes, import-time binding,
  site-packages default DB path). (R1)
- M2. Atlas frontend `devDependencies` uses
  `file:../../../frontend/packages/extension-api` — the canonical private
  starting point fails `npm install` outside the monorepo; contracts never
  exercise real npm resolution; the image build needs
  `--legacy-peer-deps`. (R5)
- M3. Event contract is stringly and silent-failing: a wrong `event_types`
  string marks the event `delivered` without invoking the handler; type
  names are undocumented. (R1)
- M4. `_DEFAULT_ENGINE` contextvar fallback fails open for workplace rules
  on any future entry point that forgets `set_policy_engine`
  (`extensions/policy.py:401-405`). (R3)
- M5. Core tools bypass the `ToolContribution` harness (no I/O schemas, no
  timeout, different review-proposal shape) — two execution disciplines to
  keep aligned. (R3)
- M6. Pre-release compatibility shims (legacy resolver inference, digest
  backfills, untagged digests, `resolves_groups: None`) become permanent at
  release. (R2)
- M7. Rehearsal fixtures are synthetic: `deps._resolve` monkeypatched,
  "legacy" rows hand-INSERTed rather than produced by running old code.
  (R5)
- M8. No mechanical import wall — the boundary holds by docs, mypy contract
  scripts, and 263 contract tests, not a mechanism. Consistent with the
  trusted-code model, but worth stating. (R3)
- M9. Three policy-action namespaces per extension write; the doc explains
  two; missing the third (`work.task.*`) when authoring policy is easy.
  (R2)

**LOW** (selected)

- L1–L4 + I1 security wording/defense-in-depth items (§12). Undocumented
  `?actions=` on `/api/capabilities`; startup-failure policy undocumented;
  `registry.policy_engine` property rebuilds the engine per access;
  `SKEIN_CORE_VERSION` hardcoded "0.2.0" fallback; tracked
  `frontend/extensions/generated.ts` rewritten by every build prehook;
  `uv build` in `backend/` leaves an un-gitignored `build/`; extension-api
  hard-codes Tailwind token classes that can drift from the host theme;
  `extension.toml` synced only by a core-repo test.

## 19. Abstractions that are clearly justified

The 12 consumed contribution slots (routes, jobs, policies, identities,
service identities, contexts, tools, specialists*, events, migrations +
`ExtensionStore`, workflow actions*, frontend nav/cards) — *with the
narrowing notes below; the `WorkItems` facade with unforgeable
command-context binding; the policy engine with monotonic combination
(core's own authority matrix now lives inside it); the durable review
pipeline with fingerprint/digest staleness binding; the versioned outbox
with per-subscriber retry budgets; the five narrow execution contexts (the
one place apparent duplication is a real boundary); build-time frontend
composition with capability gating; the manifest compatibility fields (all
five enforced); PEP 561 wheel + host-archive packaging.

## 20. Abstractions that should be narrowed

- `app.extensions.__all__`: remove or document the 8 core-plumbing symbols
  (`PolicyAPIRoute`, `PolicySubjectDep`, `decide`, `enforce_decision`,
  `subject_for`, `approval_fingerprint`, `policy_input_data`,
  `policy_input_from_data`).
- `IdentityContribution.resolves_groups`: collapse the `None`
  legacy-inference branch; require an explicit boolean pre-release.
- `ToolContribution.receipt`/`provenance`: single-legal-value fields —
  remove the fields, keep the behavior.
- `ContextContribution.version`, `SpecialistContribution.version`,
  `EventContribution.version`: bind to receipts/fingerprints or drop.
- `AppSettings`: narrow or document; it carries `api_token`.
- `ExtensionStore` doc wording: state the real guarantee
  (accident-avoidance, not isolation); optionally add a SQLite authorizer.

## 21. Abstractions that should be deferred or removed

- **Remove from `app.public`**: `WorkflowEngine`, `WorkflowContext`,
  `WorkflowResult` — "source compatibility with early packages" that cannot
  exist before the first release; keep internal.
- **Defer to 1.1**: `LifecycleContribution` (no consumer anywhere; fails
  the doc's own slot-admission rule) and `SkeinModule.requires` +
  topological sort + cycle detection (no consumer).
- **Delete pre-release**: legacy digest backfills and untagged-digest
  acceptance paths that exist only to be compatible with unreleased prior
  states of this branch.
- **Reconsider**: specialists as a code slot (the persona/flock content
  machinery already covers the demonstrated need declaratively).

## 22. Remaining core edits required by real extensions

| Need | Today's answer | Severity |
|---|---|---|
| Public command on a non-task entity (blocker, promise, decision, engagement…) | Core contribution, or degrade to REST-as-client (loses transactions/receipts/idempotency) | HIGH — first non-trivial integration hits it |
| Domain event beyond `skein.task.created/updated` | Core contribution; otherwise poll | HIGH |
| Frontend slot beyond nav + dashboard card (detail panel, page, form, notification) | Core contribution (documented as deliberate) | MEDIUM |
| Workflow timers, parallel branches, service-level escalation | Extension-owned external service | MEDIUM |
| Per-app model provider settings | Process-global config | LOW |

These are contract-extension negotiations, not forks: version ranges,
contract tests, and the composition root keep the private package intact
while core grows the surface.

## 23. Final verdict category

**Verdict B — real and usable, but broader than necessary** at the symbol
level, under-documented for true external authorship, and narrower than
the domain at the command/event level. Not A (three conservative scores
below the 8 floor: minimality 7, usability 6, ROI 6; mandated tests need
internal imports; compatibility evidence stops short of real releases).
Not C (no tested capability required internals, monorepo assumptions, or
core modifications in shipped code — the clean-room slice worked
end-to-end from built artifacts). Not D (the external-author experiment,
packaging, and enforcement evidence all exist and pass). Not E (nothing
resembles a fork: zero core edits, zero copied source, zero internal
imports in shipped code).

## 24. Confidence level

**High** on boundary reality, core adoption, security enforcement, and
packaging mechanics — all verified by execution (clean-room build from
wheels, negative probes, 550+ extension tests run, two contract scripts run
to exit 0). **Medium** on: deployment/images contracts (kubectl and docker
unavailable on this host; assessed by source reading — CI provisions both,
but only post-merge), live-browser rendering of the extension card (build
+ registry verified; no browser session), OIDC group refresh and MCP tool
paths (read, not executed), and fork-risk fractions (based on surface
counts and the repo's own admissions, not a real workplace's requirement
list).

## 25. Exact conditions required to reach Verdict A

1. Export or document a supported test-invocation surface (tool execution,
   event dispatch, registry access) so the mandated extension test suite
   needs zero internal imports; ship tests with the Atlas reference to
   demonstrate the pattern. (Clears H1; lifts usability.)
2. Close the Stage-3A documentation gaps: contribution field reference,
   event type names, operational env vars (`SKEIN_DATA_DIR`,
   `SKEIN_SCHEDULER`, auth modes), the review-approval endpoint, the wheel
   build command, and startup-failure semantics. (Lifts usability to ≥8.)
3. Trim the 1.0 surface before merge: remove `WorkflowEngine`/`Context`/
   `Result` from `app.public`; remove or document the 8 plumbing exports;
   defer `LifecycleContribution` and `requires`; drop the constant fields
   and unconsumed version fields; delete pre-release compat shims; fix the
   false "Atlas uses every supported contract" claim. (Lifts minimality and
   ROI to ≥8.)
4. Fix the Atlas frontend `file:` devDependency and exercise real npm
   resolution of the packed package in the frontend contract. (Clears M2.)
5. Grow `WorkItems` and the event catalog past task-only far enough that a
   representative second extension (not Atlas) ships without a core PR —
   ideally an independently authored one. (Lifts ROI; hardens reality
   to 9.)
6. Cut a real tagged release, then a second compatible release, and run the
   upgrade rehearsal across the published artifact pair (`upgrade-path.sh`
   stops self-skipping; CI runs contracts on PRs too). (Upgradeability to
   8 — the cap without multi-release production history.)
7. Address the security wording items (L1 `ExtensionStore` guarantee, L3
   policy-input content exposure, L4 four-eyes default) and the
   `_DEFAULT_ENGINE` fail-open fallback (M4).

Items 1–4 and 7 are days of work and belong before merge. Items 5–6 are
post-merge roadmap by nature — which is why the honest ceiling for this
branch today is Verdict B.
