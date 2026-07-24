# Synthesis of the two independent reviews — FINAL

*Inputs: `2026-07-24-agent-sol.md`, `2026-07-24-agent-fable.md`, and the
five-agent panel review of the draft (`2026-07-24-panel.md`). The
implementation plan lives in `docs/PLAN.md`. Context: solo builder,
pre-EM-transition; the manager-serving layer must be ready before day 1;
the team first sees the tool ~month 3, through value, not mandate.*

## What survived, and why

Both source reviews agreed the invariants are the product (shared service
layer, provenance, keyless determinism, gated authority, receipts,
anti-surveillance) and that the job now is **closing loops, not adding
entities**. The panel sharpened that into three governing rules:

1. **Sequence by the transition date, not by architecture.** Anything whose
   value window opens only after team adoption (month 3+) is post-transition
   work by definition, sized for evenings.
2. **Prefer fable's mechanisms with sol's rigor, not sol's mechanisms with
   fable's names.** Where the draft merged toward the heavier design
   (entity_links registry, attention budget machinery, trust partitions,
   shadow ledger), the panel cut back to the concrete version with a named
   consumer, plus a documented trigger for when to generalize.
3. **Privacy is structural, or it is nothing.** The platform's first private
   person-keyed records get: key-based auth (the repo's existing hashed
   `sk-strands-` keys — X-User identity is 403 on private surfaces), a
   separate `private.db` that backup/export/MCP/agents/FTS never open, a
   read-time-only nudge, and a canary CI test over every egress surface.
   All of it exists before the first private row is written.

## Final decisions on the draft's open questions

1. **`entity_links`?** No — over-engineering confirmed. Ship
   `tasks.waiting_on_type/waiting_on_id` and `source_finding_id` columns
   (tasks + questions). `answers`/`supersedes` already exist as dedicated
   columns. Generalize to a link table only when a fourth relationship with
   a real consumer appears.
2. **Does the privacy model hold on X-User auth?** No. Private reads/writes
   require personal-API-key identity (403 otherwise); the author-private
   journal additionally lives in a separate DB file because backups, export,
   and in-process MCP defeat any column check. Solo use gets the same
   machinery — FTS/backups are write-time sinks.
3. **Attention before manager layer?** False choice — both fit
   pre-transition once stripped to load-bearing form. Manager layer first
   (hard deadline), attention regroup second (it's ~a day as a refactor of
   `briefing.py`).
4. **Minimal reviewer ergonomics?** Batch approve (prerequisite for meeting
   ingestion's per-line proposals) + optional quick-select rejection reasons
   prefilling the existing notes field. Diff view second wave.
5. **Over-specified for one person?** Most of draft Phases 1, 3, and 5:
   thread view, legality registry, attention budget/acks, auto-quiet, shadow
   mode, trust partitions, token budget config, coordination-debt metric —
   all cut or deferred with triggers.

## Cut list (with re-entry triggers)

| Cut | Trigger to revisit |
|---|---|
| Shadow authority level | Proposal volume overwhelms the review queue (the review queue *is* shadow mode today: zero-risk proposals + grading + trust scores) |
| `entity_links` table + registry + thread view | A 4th typed relationship with a named consumer |
| Attention budget / ack states / dedupe keys | Real duplicate-notification pain (findings already dedupe weekly) |
| Trust profile partitions by model version | A model swap produces a problem review stats didn't catch |
| Auto-quiet findings rules | Rule count or noise grows beyond hand-tending (maintainer retires rules at season end today) |
| Stakeholder signed status pages | Real stakeholder demand AND real auth; then build as push-generated static artifacts, never by exposing the app |
| Coordination-debt / closed-loop-rate metrics registry | Multi-team scale |
| sol's Playbooks 2.0, delegation contracts, evidence pack, outbox, capability broker | Unchanged from draft — deferred |
| Employee private-prep sections (sol §11) | Refused until the journal's separate-store pattern is proven |

## Security hard lines (from the panel, now policy)

- The app is never exposed beyond the trusted LAN while identity is a header.
- No agent/LLM read path over the feedback journal, ever (memory injection +
  FTS + OTEL = four sinks from one touch). No embeddings of private content.
- No stored aggregation over the journal; the "no entry for X in N weeks"
  nudge is computed at read time on the author's page only.
- Private writes log no content anywhere team-visible; `private.db` keeps its
  own audit table (documented narrowing of the provenance norm).
- Meeting ingestion skips-and-flags `fb:`-prefixed lines; never routes them.
- `/api/admin/export` gets auth (it is unguarded today) and never includes
  private tables.

## The shape of what ships

**Wave 1 (pre-transition, ~2 weeks):** privacy foundation + private notes +
`fb:` journal + per-person 1:1 brief · meeting-notes ingestion + batch
approve · manager commitment ledger (audience) · My Day attention regroup
with reasons · finding dispositions (keyed rule+subject) + convert-to-work ·
experiments + close conclusions · manager-onboarding playbook · review
`claim_at` + transaction-wrapped approve · (treat: ICS, diff view)

**Wave 2 (early post-transition, evening-sized):** review diff view ·
`waiting_on` receipts in health/forecast · decisions `category` + charter
view · skills/growth field on what-if · `/ask` citations · `authority_stale`
findings rule

**Wave 3 (post-adoption, month 3+):** golden-trace evals (scriptable mock
provider first) · per-engagement context pack filter · weekly pulse question
· disposition analytics · shared 1:1 agendas if demanded · incident timeline
after the first incident

Full specs: `docs/PLAN.md`. Panel detail: `2026-07-24-panel.md`.

**Standing guardrail (from both reviews):** every post-transition build runs
as a Skein experiment engagement — timebox, kill criteria, recorded
conclusion. The platform polices its own player-coach trap.
