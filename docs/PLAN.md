# Implementation plan — post-review roadmap (2026-07-24)

Derived from `docs/reviews/2026-07-24-synthesis.md` (final) and the
five-agent panel (`docs/reviews/2026-07-24-panel.md`). Three waves.
Wave 1 must land pre-transition. Every wave-2/3 item must fit in ≤2
evening-sized sessions or be split until it does.

Conventions that apply to every item below:
- Services are the only write path; REST + (where agent-facing) gated tools.
- Every platform write logs to `activity` — EXCEPT private-record writes,
  which log to a local audit table inside `private.db` (documented
  narrowing; see W1.1).
- Every new scheduled sweep is a `JobSpec` in `services/jobs.py` — never a
  bare `scheduler.add_job` (gets `/health` + `job_stale` coverage free).
- New schema = new numbered migration; CHECK-constraint changes on existing
  tables use the 011 copy-rename rebuild pattern.
- Keyless-first: no feature below requires an LLM.

---

## Wave 1 — pre-transition (~2 focused weeks)

### W1.1 Privacy foundation + private notes + feedback journal  (~3 days)

The platform's first private person-keyed records. Everything here ships in
ONE change so no private row ever exists before the machinery does.

**Storage** — new `services/private_notes.py`:
- Separate SQLite file `data/private.db` (path from `config.PRIVATE_DB_PATH`,
  env `STRANDS_PRIVATE_DB`). Own `connect()`; NEVER uses `app.db.connect`.
- Schema (created via `CREATE TABLE IF NOT EXISTS` on first use — single
  consumer, no shared migration chain):
  - `private_notes(id, author TEXT, person TEXT, kind TEXT CHECK(kind IN
    ('note','feedback')), body TEXT, created_at TEXT)`
  - `private_audit(id, author TEXT, action TEXT, note_id INTEGER,
    created_at TEXT)` — provenance stays, content never leaves the file.
- Structurally excluded by construction: `admin.backup()`/`export()` touch
  only `platform.db`; `index_record` is never called; no agent tool, no MCP
  tool, no `review._registry` entry may reference private entities (add a
  test asserting the registry and `ALL_TOOLS` contain no private names).

**Auth** — `routes/deps.py` gains `StrongUser`: identity accepted ONLY from
a strong credential; X-User-resolved identity → 403 with a message pointing
at `POST /api/keys`. All `/api/private/*` routes use it. Today "strong"
means a personal `sk-strands-` API key; the deployed target is **OIDC +
PKCE** (ported from the user's other project), so `StrongUser` is the single
swap point — when OIDC lands, a validated ID-token session satisfies it and
nothing else changes. Also: gate `GET /api/admin/export` behind `StrongUser`
(it is unguarded today).

**Frontend key support** — `lib/api.ts`: optional API key stored in
localStorage (`strands-key`), sent as `Authorization: Bearer` when present;
settings field next to the name picker. Required for the People page.

**Endpoints**:
- `GET/POST /api/private/notes?person=` — list/add (kind note|feedback).
- `GET /api/private/brief/{person}` — deterministic 1:1 brief from
  team-visible data only: their standups, open blockers, questions assigned
  to them, commitments, tasks completed since a given date. Degrades to
  empty sections pre-adoption.

**`fb:` capture prefix** — in `services/capture.py`, short-circuit BEFORE
classification: `fb: <person> — <note>` (also accepts `:` separator).
Refuses non-human origin. Requires key-authenticated request (palette works
once the key is set); otherwise returns an instructive error, never a note.
No `index_record`, no `log_activity` — audit goes to `private_audit`.
Guard: this branch must land in the same commit as the journal (today the
prefix would fall through to a team-visible FTS-indexed note).

**Nudge** — computed at read time in the People page response only
("no feedback note for {person} in 21+ days"). No notifications row, no
Slack, no stored counters, no findings rule.

**Frontend** — `/people` page: person picker, brief panel, notes timeline,
add-note box, read-time nudge line. Nav entry.

**Canary CI test** — `tests/test_privacy.py`: write canary-string private
notes, then assert the canary is absent from: `/api/search`, semantic search
path, published context pack (DB + artifact file), digest markdown AND the
digest's saved note row, `/api/admin/export` output files, `/api/activity`,
`/api/notifications`, all `data/artifacts/` files. Assert `private` tables
absent from `admin.TABLES`, backups don't copy `private.db`, no agent tool
or registry entry names a private entity, and `fb:` capture without key
auth stores nothing.

### W1.2 Meeting-notes ingestion + batch approve  (~2.5 days)

**Refactor first**: wrap `review.approve_change`'s registry apply in
`db.transaction()` (it predates the transaction manager; removes the
partial-apply failure class before ingestion multiplies proposals).

**`services/ingest.py`** — `ingest_notes(text, actor)`:
- Deterministic only (LLM assist deferred). Size caps: 64 KB / 500 lines.
- Per line: strip bullets/timestamps; skip blank/short; lines starting with
  a private prefix (`fb:`) are counted, flagged in the response, and NEVER
  stored or routed; remaining lines run through `capture.classify`; each
  classified line becomes `review.propose_change(entity=<kind>,
  payload=…, proposed_by=actor, origin='human')`.
- Returns `{proposals: n, skipped_private: n, unclassified: [lines]}`.
  Raw transcript is never persisted.

**Batch approve** — `POST /api/review/approve-batch {ids: []}` looping the
existing CAS-safe `approve_change`; response reports per-id outcome.
Frontend `/review`: checkboxes + "approve selected". Optional quick-select
rejection reasons (wrong_entity / wrong_content / wrong_time / duplicate)
that prefill the existing notes field — no schema change.

**Frontend** — `/ingest` page (textarea → result summary → link to
`/review`). Nav or My Day entry point.

**Tests**: classification routing, `fb:` line refusal, size caps, proposal
provenance, batch approve CAS behavior (double-approve race stays safe).

### W1.3 Experiments + close conclusions  (~1.5 days)

**Migration 014** (platform.db): `engagements` ADD COLUMN `kind` TEXT NOT
NULL DEFAULT 'delivery' (delivery|experiment), `timebox_end` TEXT,
`kill_criteria` TEXT DEFAULT '', `outcome` TEXT DEFAULT '', `conclusion`
TEXT; backfill `conclusion='unmeasured'` for already-closed engagements.

**Services**:
- `create_engagement(kind=, timebox_end=, kill_criteria=, outcome=)`;
  intake accept passes an optional outcome statement through.
- `update_engagement`: NEW closes require `conclusion` ∈ (achieved, partial,
  missed, invalidated, unmeasured, stopped). Experiment closes auto-draft a
  lesson prefilled from outcome + conclusion. Ship-it recap frames
  `invalidated` as a concluded experiment, not a failure.
- `portfolio.slip_forecast`: skip milestones of experiment engagements
  (join `m.engagement_id` → `e.kind`).
- `insights`: new rule `_r_experiment_overdue` — open experiments past
  `timebox_end` with no conclusion. Append to `RULES` (15th rule ID; update
  docs/INSIGHTS.md and FEATURES.md counts).

**Sweep**: `seed.py` + existing tests that close engagements get
conclusions. Frontend: kind/timebox/kill-criteria on intake accept +
engagement forms; outcome + conclusion in engagement header, dashboard
card, exec readout.

### W1.4 Finding dispositions + convert-to-work  (~1.5 days)

**Migration 015**: `finding_dispositions(id, finding_id, rule_id TEXT,
subject TEXT, disposition TEXT CHECK IN ('dismissed','deferred','converted',
'resolved'), reason TEXT DEFAULT '', deferred_until TEXT, created_by,
origin, created_at)` + index `(rule_id, subject)`. Also `tasks` and
`questions` ADD COLUMN `source_finding_id` INTEGER.

**Key design point (architect)**: findings re-fire weekly as new rows, so
suppression keys on `(rule_id, subject)`, not `finding_id`:
- `run_findings` insert loop consults latest disposition per (rule_id,
  subject): skip when `dismissed` within 28 days or `deferred_until` in the
  future. (`resolved`/`converted` do NOT suppress — a re-fire after a fix is
  signal.)
- `digest_findings` excludes findings that have any disposition.

**Service** (`insights.py`): `disposition_finding(finding_id, disposition,
reason, deferred_until, actor)` — human-only (not in `review._registry`);
logs activity. `convert_finding(finding_id, kind task|question, title)` —
creates the record with `source_finding_id`, dispositions as `converted`.
Per-rule counts (fired / dispositioned / converted) added to `insights()`.

**Routes/UI**: `POST /api/findings/{id}/disposition`, `POST
/api/findings/{id}/convert`; `/insights` gets dismiss/defer/convert buttons
+ per-rule acted-on column.

### W1.5 Small manager-layer items  (~1 day total)

- **Commitments audience**: migration adds `commitments.audience` TEXT NOT
  NULL DEFAULT 'external' (external|team); service/route filter; portfolio
  page splits "External commitments" / "My commitments to the team".
- **`manager_onboarding.yaml` playbook**: listening-tour tasks per report,
  month-2 share-back milestone, 30/60/90 checkpoint events, first operating
  review ritual.
- **Attention regroup**: `briefing.my_day` items gain `group`
  (decide|unblock|commit|review|notice) + `reason` string (receipts already
  computed); My Day renders grouped; `attention_count` counts decide+unblock.
  Refactor of `briefing.py`, no new service, no new tables.
- **Review `claim_at`**: migration column on `pending_changes`; `_claim`
  stamps it; `review_stats` adds median claim→verdict (the honest review-
  burden proxy).

### W1.6 Treats (only if the wave is done)

- **ICS feed**: `GET /api/calendar.ics` — stdlib string building over events
  + milestone/commitment due dates (all team-visible). LAN-only; if
  `STRANDS_API_TOKEN` is set, require `?token=`. Document that hosted
  calendar clients would mirror titles off-LAN — recommend local clients.
- **Review diff view**: for update-type proposals, render current row vs
  payload two-column. Needs a read-fn map per registry entity — scope to
  task/milestone/engagement updates first.

**Wave 1 exit criteria**: canary test green in CI · a pasted meeting note
becomes review proposals with `fb:` lines flagged · a 1:1 prep view exists
for every report with zero team adoption · closing an engagement demands a
conclusion · findings can be dismissed/converted and dismissals suppress
re-fires · all five lint gates + full test suite green.

**Wave 1 shipped 2026-07-24** (+ a three-agent review pass: security,
correctness, spec compliance — all confirmed findings fixed). Recorded
deviations from the spec above:
- Key minting hardened beyond spec: `POST /api/keys` requires an existing
  key; first key per person via `python -m app.bootstrap_key <name>`
  (out-of-band). The review proved self-service minting on X-User identity
  defeats the entire private-record boundary.
- The canary test is EXHAUSTIVE (every platform table via sqlite_master +
  every file under DATA_DIR), not an enumerated surface list.
- ICS uses a dedicated `STRANDS_ICS_TOKEN`, never the API token; fail-closed
  when the API is token-locked. Multi-line captures containing `fb:` are
  refused whole; chat refuses `fb:` before the agent/session/model sees it.
- Portfolio shows commitments as one list with team/external markers rather
  than two sections (deliberate; revisit if it reads badly in use).
- Badge counts decide+unblock+commit+review (not just decide/unblock);
  notice tier stays badge-silent.
- Deferred to Wave 2 from the review: private-note delete/retention +
  author-readable audit view; per-user rate limits on capture/ingest.

---

## Wave 2 — early post-transition (evening-sized increments)

| Item | Spec sketch | Size |
|---|---|---|
| Review diff view (if not done in W1.6) | as above | 1–2 sessions |
| `waiting_on` edges | Migration: `tasks.waiting_on_type` ('task'\|'blocker'\|'commitment') + `waiting_on_id`; set/clear in `work.update_task`; surfaced as health receipts + slip-forecast annotation ("blocked behind commitment #12"). NOT a link table. | 1–2 sessions |
| Decisions `category` | Migration: `decisions.category` TEXT DEFAULT '' ('charter' initially); charter = filtered decisions view page; risks stay decisions with `review_by` (documented pattern, no new feature) | 1 session |
| Skills/growth field | `users.growth_interests` TEXT; shown on what-if staffing page (display only, no matching logic) | 1 session |
| `/ask` with receipts | Relabel of FTS search: answer = top snippets with entity#id citations; optional LLM synthesis when a provider exists | 1 session |
| `authority_stale` findings rule | Fires when an `autonomous`/`notify` grant is >90 days old — the half-life as a nudge, not a demotion state machine | 1 session |

**Wave 2 shipped 2026-07-24** (+ three-agent review; all confirmed findings
fixed). Recorded deviations:
- `/ask` LLM synthesis deliberately skipped (keyless citations ARE the
  answer); endpoint-only, no UI surface yet.
- Growth interests display in the what-if API payload only (no what-if UI
  page exists); setter is the 🌱 link on My Day.
- Setting `waiting_on` bumps `updated_at` (declaring stuckness resets the
  stale clock) — deliberate: the waiting receipt itself keeps the task
  visible, and declaring a dependency IS activity.
- Charter entries require `review_by`; category survives supersession.
- authority_stale falls back to `updated_at`+90d when `review_by` is NULL
  (migration 018 backfills pre-017 grants).

## Wave 3 — post-adoption (month 3+, team is using it)

| Item | Notes |
|---|---|
| Golden-trace evals | Pre-task: scriptable scenario provider (MockAgent can't do it — it's a hardcoded router). Then 5 scenarios: fixture + prompt → expected tool-call sequence. CI job. |
| Per-engagement context pack | `publish_pack(engagement=)` filter — the one context "view" with a named consumer (delegated agents) |
| Weekly pulse | `POST /api/feedback kind=pulse` + one Monday digest question: "did Skein reduce coordination effort this week?" |
| Disposition analytics | Acted-on / false-positive per rule over real volume; retire rules at season end (manual, per FEATURES.md) |
| Shared 1:1 agendas | Only if reports ask for it after seeing the manager-side workflow; participants-visibility tier designed then, not before |
| Incident timeline | After the first real incident flows through Skein — draft from activity ledger, human edits, archive as artifact |

**Wave 3 shipped 2026-07-24** (golden traces, engagement packs, pulse,
disposition analytics). Recorded deviations:
- Golden traces pin the TOOL layer (trajectory, policy compliance, final DB
  state through the real gate) — what a model would *choose* to call is
  untestable keyless; the scriptable-provider idea was dropped as
  model-theater. Scenarios live in `tests/test_golden_traces.py`, run in CI.
- Pulse shipped pre-adoption (buttons on My Day + Monday digest line) — the
  tally stays empty until the team arrives, which is honest.
- Pulse votes are stored and logged UNATTRIBUTED (no created_by, ledger gets
  neither actor nor verdict; migration 020 scrubbed early rows) — a
  documented narrowing of the provenance norm: the vote is honest only if it
  cannot be attributed. Regression-tested across feedback/activity/export.
- Engagement packs are agent-reachable (chat tool + MCP get_context_pack
  accept engagement_id); the CLI has no --engagement flag yet.
- Shared 1:1 agendas + incident timeline REMAIN deferred (need real usage).

## Deferred with explicit re-entry triggers

See the cut table in `docs/reviews/2026-07-24-synthesis.md`. Notables:
shadow mode (trigger: review-queue overload), `entity_links` (trigger: 4th
relationship), stakeholder pages (trigger: demand + OIDC landed; then
push-static or behind OIDC, never header-auth exposure).

**OIDC + PKCE is the planned deployment auth** (ported from the user's other
project, not rebuilt here). Until it lands: the app stays LAN-only and
`StrongUser` = personal API key. When it lands: `StrongUser` accepts the
OIDC session, X-User goes away outside dev/mock mode, and the beyond-LAN
feature triggers (stakeholder pages, hosted-calendar ICS) unlock.

## Standing guardrails

1. Every post-transition build is itself a Skein experiment engagement:
   timebox, kill criteria, recorded conclusion.
2. No agent/LLM/MCP path over private records — enforced by test, revisited
   never.
3. The app never accepts non-LAN traffic while identity is a header.
4. New scheduled work goes through the `JOBS` registry, no exceptions.
5. A feature ships only if it reduces coordination effort, improves a
   decision, makes delegation safer, or closes a loop (sol's bar).
