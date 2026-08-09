# Spec: Flocks

A flock is a named group of bench personas called into chat with one message.
The message fans out to every member in parallel. Each member answers under
its own name, in its own section. An optional synthesis merges the sections.
Each flock turn records a trace, which feeds a diamond visualization on the
agents surface.

The name is the brand's own word: a skein is a flock of geese in flight.
Personas are strands; a flock is several strands called into formation.

Status: BUILT. `docs/FEATURES.md` carries the shipped summary; this file
stays as the design record and the reason behind each rule.

## Objective

A single persona gives one lens. Real decisions want the tension between
lenses — the architect's tradeoff space against the reviewer's failure modes
against the minimal-change engineer's restraint. Today a user must run
`/as <persona> <message>` once per lens, by hand, in sequence, and merge the
answers in their head.

User stories:

- A team member types `/flock engineering Should we shard the database?` and
  reads three sectioned answers, each signed by its persona, in one turn.
- An operator defines a flock in a YAML file. A keyless deployment still
  lists it, validates it, and answers deterministically in mock mode.
- A sponsor reads every write a member proposed in the review inbox — no
  member write applies directly from a flock turn.
- Mission control shows the diamond for a past flock turn: who ran, how
  long each took, what each proposed, what the turn cost.

Success looks like: one message in, N attributed perspectives out, every
side effect review-gated, every model call accounted per member.

## Non-goals

- **No orchestration engine.** The diamond — dispatch, members, optional
  synthesis — is the only shape. No nesting (a member is a persona, never
  another flock), no chains, no conditional routing. Flocks add one more
  fixed pattern, not a framework.

  Amended 2026-08-07. This bullet used to end "the codebase has exactly one
  sub-agent today (the planner in `team_agent.py`)". Three tool-carrying
  sub-agents now exist: the planner, a flock member, and a specialist the
  Chief of Staff consults (`consult_specialist`, added so "ask @code-reviewer about
  tomorrow's plan" reaches the bench instead of filing a task for it).
  The non-goal itself stands, and the consult is bounded to keep it standing:
  depth is one hop, enforced by construction rather than by a counter —
  `build_agent` creates the tool only when `persona == ""`, so a specialist
  never holds it and cannot consult anything. No chains and no routing.
  Auto-selection is not designed for — the orchestrator is instructed to
  consult only a specialist the user names — but it is BOUNDED rather than
  prevented: `MAX_CONSULTS_PER_TURN` caps a fan-out the model chose on its
  own. Read this as one more fixed pattern, not as the engine this bullet
  refuses.
- **No debate rounds.** Members answer independently and never see each
  other's sections. Synthesis is the only merge point.
- **No new identity kind.** A flock is not an identity and never writes as
  itself. Members are the identities — their slugs already exist in the
  authority matrix and trust scores.
- **No runtime CRUD.** Flock definitions are files edited like code (the
  playbooks precedent). No POST /api/flocks.

## Tech stack

No new dependencies. Backend: FastAPI + the existing Strands agent layer +
SQLite. Frontend: Next.js + Tailwind, hand-rolled SVG for the diamond — no
graph library (the layout is fixed and small; physics would add jitter and
a dependency for nothing).

## Definitions

`backend/flocks/*.yaml`, one file per flock, slug = filename stem:

```yaml
# backend/flocks/engineering.yaml
name: Engineering
description: Design tension on one question — the architecture, the failure modes, and the smallest thing that works
emoji: 🛠️
members:            # 2-4 bench persona slugs, order = section order
  - backend-architect
  - code-reviewer
  - minimal-change-engineer
synthesis: false    # optional, default false — the +1 model call is opt-in
```

- Overlay: `SKEIN_FLOCKS_DIR`, same semantics as `SKEIN_PLAYBOOKS_DIR` —
  loaded alongside stock, same slug replaces the stock file, a configured
  directory that does not exist surfaces in `overlay_errors()`.
- Validation is two-pass, the personas precedent: runtime parsing is
  lenient (a malformed file drops off the list rather than 500ing chat);
  `validate_all()` is the strict pass wired into `lint.sh`. Strict rules:
  - 2–4 members, all existing bench slugs, no duplicates
  - flock slug matches the persona slug charset and does not collide with
    a bench slug — `/flock <slug>` and `/as <slug>` share a namespace in
    the user's head, and a collision makes one of them unreachable
- Voice: `description` may carry the product voice (nothing is asked of
  the reader). Errors from validation follow STE.

## Service and REST (the deterministic core)

- `backend/app/services/flocks.py` — loading, validation, trace recording.
  All SQL lives here. Tools and routes wrap it, never bypass it.
- `GET /api/flocks` — list: slug, name, description, emoji, members
  (resolved to name + emoji), synthesis flag.
- `GET /api/flocks/traces?thread=…|flock=…&limit=…` — trace rows for the
  diamond view.
- Migration: new numbered file in `backend/migrations/`, append-only.
  One row per flock turn:

```sql
CREATE TABLE flock_traces (
  id         INTEGER PRIMARY KEY,
  thread_id  TEXT NOT NULL,
  user       TEXT NOT NULL,
  flock      TEXT NOT NULL,
  -- JSON: [{slug, status, ms, receipts, tokens_in, tokens_out}]
  -- Tokens live IN the trace: usage rows are keyed by thread + agent_name,
  -- so two flock turns in one thread with the same members are
  -- indistinguishable in a join — per-turn cost is not derivable from the
  -- usage table alone.
  members    TEXT NOT NULL,
  -- NULL when synthesis is off; JSON {status, ms, tokens_in, tokens_out}
  -- when on — the diamond's bottom node needs data, not a flag.
  synthesis  TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_flock_traces_thread ON flock_traces(thread_id);
CREATE INDEX idx_flock_traces_flock ON flock_traces(flock);
```

Member `status` vocabulary: `ok | failed | cancelled`.

Everything above works with `SKEIN_MODEL_PROVIDER=mock` and no keys.

## Chat protocol

- `/flocks` — engine command in `agents/commands.py` (deterministic, every
  provider): lists flocks the way `/personas` lists the bench.
- `/flock <slug> <message>` — route-level command in `routes/chat.py`
  (needs the agent layer), the `/as` precedent. It MUST be registered in
  `commands.COMMANDS` with `handler: None`: `dispatch` answers any
  unregistered `/word` with a did-you-mean generator before the route sees
  it, so an unregistered `/flock` would be intercepted (and offered
  `/flocks`). It does not run in Slack or the CLI. Slack refuses it by
  name: `routes/slack.py` matches EVERY handler-None command, because
  `dispatch` returns None for one and Slack's MockAgent would then
  smart-capture the raw slash command as a note against the person who
  typed it. The CLI has no chat command at all.
- Unknown slug streams an SSE error. When the argument matches the slug
  charset: `Flock '<slug>' is not defined. Run /flocks to list them.`
  When it does not, the rejected value is never echoed: `That is not a
  flock slug. Run /flocks to list them.`

Execution of a flock turn:

1. `ensure_user(member, kind="agent")` per member at turn start — the `/as`
   precedent. A slug a human already claimed is a member construction
   failure, isolated per step 7. Build one agent per member — fresh and
   stateless: `build_agent` grows a `stateless=True` parameter that skips
   the session manager and conversation-manager state (today it always
   attaches one for real providers, `team_agent.py:460`). No `--<slug>`
   thread suffix, prompt is the user's message only. The persona's own
   `tools` allowlist applies as usual, and a member gets NO MCP tools: a
   remote call reaches neither the gate nor the receipt box, so a member
   holding one would write to a third party while its trace row reported it
   proposed nothing.
2. Run members concurrently (asyncio tasks). Identity is set per task with
   `set_agent_identity(member)` inside each task — contextvars are
   task-local, so member identities cannot bleed into each other's writes.
   The route sets `set_requester_identity(user)` BEFORE creating the tasks
   (tasks inherit the parent context copy), so every member proposal
   records the asking human as `requested_by`.
3. Stream sections in declared member order. The head-of-queue member
   streams live; completed members flush as they reach the head. Each
   section ALWAYS opens with the masthead (`###`, emoji, name, vibe) — the
   once-per-thread `thread_contains` dedup does not apply, because in a
   flock turn the masthead is the section delimiter, not a repeated
   introduction. Who answered never depends on whether the model signs
   its work. A heading, not bold text, and a `---` rule between members
   (never above the first): the whole turn is ONE assistant bubble, so the
   section break has to carry weight that `**` does not. `.prose-chat` in
   `frontend/app/globals.css` sizes the levels — Tailwind v4 preflight
   resets headings to body size, so the CSS is what makes the hierarchy
   visible at all.
4. **Every member write is a review proposal**, regardless of
   `SKEIN_AGENT_REVIEW` and of the member's earned authority level. The
   enforcement point is a `force_review` contextvar in `agents/identity.py`,
   set inside each member task alongside the identity, consulted by
   `gated_write` before the level branch. Precedence: `forbidden` still
   refuses (it never softens into a proposal) > flock-forced review >
   matrix level. A flock turn is consultative: the user asked for
   perspective, and N agents acting autonomously on one message is the
   failure mode this rule prevents. Because writes queue unconditionally,
   member construction passes a flag so the persona system prompt states
   review is ON — the stock prompt would otherwise claim writes apply
   directly, and the model would misreport its own writes.
   Four writers skip `tools/_gate.py` BY DESIGN (the delegation trio and the
   handoff generator — `tests/test_gate_coverage.py::UNGATED_WRITERS` holds
   the list, derived from a gate spy rather than declared). force_review
   cannot reach them, so each carries `identity.refuse_when_consultative` and REFUSES
   in a flock rather than queuing: status motion and a projected artifact
   have no proposal shape, and the member was asked for an opinion, not for
   work. A new ungated writer fails that test until it decides.
5. Receipts: each member task calls `receipts.start()` and drains INSIDE
   the task, forwarding drained receipts through its own event queue
   tagged with the member slug. The receipt box is a plain list in the
   context — contextvar isolation does not isolate mutation of a shared
   list, so a route-level `start()` would interleave N members' receipts
   unattributed. Per-member draining is also what makes the `receipts`
   count in the trace computable.
6. If `synthesis` is on: one more model call, no tools, input is the member
   sections, streamed last under its own masthead. It writes nothing. The
   merge masthead is `##`, one level ABOVE the members' `###`: it merges
   the sections above it, and giving it their level renders the turn as
   N+1 peer voices with no visible seam where the merge starts.
7. Errors are isolated per member: a failed member's section states
   `<Name> did not answer.` plus the error line, and the turn continues.
   Synthesis runs over the survivors. Only when every member fails does
   the turn emit an error frame. A provider-level fault (bad model id)
   fails at construction and uses the existing SSE error path.
8. Cancellation (stop button, tab close): the sync finally cancels all
   member tasks with `task.cancel()`, reads whatever metrics each agent
   accumulated, and records `status: cancelled` for unfinished members in
   the trace. Uncancelled member tasks would keep running — and keep
   filing proposals — after the user hit stop.
9. Accounting: `_log_usage` per member with `agent_name=<member slug>` —
   the mechanism exists. Synthesis logs `agent_name=<flock slug>`. The
   cost multiplier is therefore visible in `/api/usage` per head.
10. Transcript: the whole turn logs under the base thread id as one
    assistant message containing all sections (the existing pattern — the
    suffixed session ids never appear in `chat_messages`). The turn is
    also bridged into the Chief-of-Staff model session the way command
    turns are, so a follow-up like "what did the reviewer say?" has the
    sections in context. The turn guard is SKIPPED for flock turns: they
    are consultative, and a filing-shaped message belongs in a normal
    turn — there is no single agent for the objection re-prompt to
    address.
11. Trace: one `flock_traces` row written when the turn closes, in the
    same close path that logs usage.

Rate limiting: a flock turn charges the `chat` bucket ONE SLOT PER MEMBER,
plus one more when the flock synthesizes (`ratelimit.check(..., cost=...)`),
not the single
slot a turn used to cost. A member is an agent loop, not one call — a
measured 3-member turn ran 9 model calls — so a per-turn charge let one
message buy several turns of model spend. The charge is all-or-nothing and
happens after the flock resolves and before the stream opens, so no model
runs. THAT CALL spends nothing when it refuses — the turn's own top-of-route
chat slot is already spent by then and stays spent. It is deliberately the chat bucket and not a bucket of
its own: two buckets would let a refused flock silently consume chat slots,
and the operator would have no single number for a person's model budget.

The per-actor buckets are NOT covered by that: the `write` bucket
(`tools/_gate.py`) keys on the (member, requester) pair, so a 4-member flock
carries 4x the write budget — but two people flocking the same persona no
longer share it, each holds their own bucket against each member. The
`memory` bucket (`services/memory.py`) still keys on the member slug alone
and stays shared across requesters. Every member write is review-gated
(step 4), so the amplification fills an inbox, not the database.
`SKEIN_MONTHLY_BUDGET_USD` reports overspend and never refuses.

Mock mode: each member yields a deterministic reply from a flock-specific
pool — NOT the MockAgent freeform path, whose smart-capture writes to the
database ungated (`mock_agent.py`); routed through it, one flock message
would file N duplicate captures attributed to the human. Mock members
dispatch no commands and write nothing. Mock replies are one of the five
voice pools CLAUDE.md commits to feeding — per-persona mock lines are
welcome here. Mock synthesis is a plain count line (it carries a number,
so it is never warm).

## Frontend

- Chat needs **zero new components** for the MVP: mastheads and sections
  are markdown the transcript already renders, receipts already render.
- Diamond trace view: a section on `frontend/app/agents/` (mission
  control, already the agents-as-teammates surface). Fixed-layout SVG:
  requester at top, members in a middle row, synthesis (or the merged
  answer) at the bottom. A node shows emoji, name, duration, receipt
  count. Data from `GET /api/flocks/traces`. Empty state may be warm.
  Theme-aware via the existing token system in `lib/theme.ts`.
- The diamond earns its place by showing what the transcript obscures:
  parallelism, relative latency, and which member actually proposed
  writes. It is a trace view, not an org chart.

## Commands

```bash
# backend (from backend/)
.venv/bin/pytest tests/test_flocks.py     # feature tests
.venv/bin/pytest                          # full suite
# frontend (from frontend/)
npm test                                  # vitest, includes trace view
npm run build
# repo root — the exact CI gate, run before every commit
./scripts/lint.sh                         # includes flocks validate_all
```

## Code style

Match the house style. The service reads like `services/personas.py`
(lenient runtime parse, strict `validate_all`), the route change reads
like the `/as` block in `routes/chat.py`, comments follow the CLAUDE.md
rules (constraint + consequence, no narration). Example of the expected
comment register:

```python
# identity is set INSIDE each member task: contextvars are task-local,
# and setting it outside the task would sign every member's proposals
# with the last slug assigned
```

## Testing strategy

Backend (`tests/test_flocks.py`, pytest, mock provider — deterministic):

- Definition loading: valid file lists; malformed file drops off at
  runtime and fails `validate_all`; overlay wins a slug collision.
- Validation: <2 members, >4 members, unknown member, duplicate member,
  bench-slug collision — each refused with an STE error.
- REST: `GET /api/flocks` shape; traces endpoint filters by thread/flock.
- Chat: `/flocks` lists; `/flock` with an unknown charset-valid slug names
  it, with an invalid one echoes nothing; a mock flock turn streams N
  sections in declared order plus the done frame, files NO captures and
  writes NO records; the transcript logs one assistant message; a trace
  row lands with per-member status.
- Gating: `gated_write` called directly with the flock context set
  (member identity + `force_review` + requester) queues a proposal signed
  by the member with `requested_by` = the human, even with
  `SKEIN_AGENT_REVIEW=0` and the member at `autonomous` in the matrix;
  `forbidden` still refuses outright. Tested at the gate, not through
  mock chat — mock members never reach `gated_write` by design.
- Isolation: one member raising leaves the other sections intact and
  records `failed` in the trace row.

Frontend (vitest): trace view renders the diamond from fixture JSON;
member failure state renders. Playwright: the existing agents-page smoke
plus axe extends to the trace section.

## Boundaries

- **Always:** writes go through `services/`; every write records origin and
  `created_by` = member slug; user-visible strings follow STE (warmth only
  where nothing is asked of the reader); schema changes are new numbered
  migrations; `./scripts/lint.sh` before every commit.
- **Ask first:** raising the member cap; giving members thread history;
  making synthesis default-on; any new env var beyond `SKEIN_FLOCKS_DIR`;
  any frontend dependency (the answer is no, but ask); a member-write mode
  that bypasses the review gate.
- **Never:** branch on a provider name outside `team_agent.py::_model()`;
  SQL in a route or tool; a flock slug that collides with a bench slug;
  editing an applied migration; letting a flock turn take down the REST
  API when the provider is misconfigured (mock degradation applies).

## Success criteria

1. With `SKEIN_MODEL_PROVIDER=mock` and no keys: `/flocks` lists the stock
   flocks, `/flock engineering <q>` streams sectioned deterministic
   replies and a done frame, a trace row exists, all tests pass.
2. With a real provider: members run concurrently (turn wall-clock is
   near the slowest member, not the sum), each section carries its
   masthead, member writes appear in the review inbox under the member's
   name, `/api/usage` shows one row per member for the turn.
3. A malformed flock file: chat and REST keep working, the file is absent
   from `/api/flocks`, `./scripts/lint.sh` fails naming the file.
4. The diamond renders a real trace on the agents page in both themes and
   passes the axe check.
5. `./scripts/lint.sh` and the full backend suite pass.

## Open questions — closed (2026-08-09)

- Stock flocks: two shipped, as the lean said — `engineering.yaml` and
  `delivery.yaml` (project-shepherd, sprint-prioritizer, meeting-notes).
- The synthesis masthead did not get the goose. A merge section asks the
  reader to weigh answers, and the goose is earned only where nothing is
  asked of the reader.
- Trace retention: keep, as the lean said — nothing prunes
  `flock_traces`. Rows are small and it is not the hash-chained ledger.
