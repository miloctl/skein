# Ideation run — 2026-07-25

Five agent perspectives (Chief of Staff, Product Manager, AI Engineer,
Developer Advocate, Whimsy Injector) each read FEATURES.md / ROADMAP.md /
relevant source and proposed ranked features plus any outright fixes they
noticed. Status: **fixes applied** in `6534e96`. Since then (2026-07-27): **P5
shipped** (standup 'yesterday' derives from your activity on My Day — CLI
--draft flag still open), **S7-style req: capture shipped**, the
**chat-identity contextvar fix shipped** (personas run on it), the
onboarding key step is now self-serve ("Request a key"), and A2's
prerequisite (strong-verdict provenance, `reviewed_strong`) exists.

> **CLOSED 2026-08-02.** The 2026-08-02 consolidation checked every remaining
> proposal against the code and moved the open ones to `docs/ROADMAP.md` under
> "Open backlog" — including the residuals of partial ships (P5's CLI `--draft`
> flag, D1's `--all-from`, D4's `ask`/`week`). C1, P2, A1, A2 and the D1 core
> verbs were already built and were dropped. This file is kept for the
> mechanism sketches and the per-agent reasoning behind each proposal.

## Cross-agent convergences

1. **Close the delegation loop** (A1, echoed by PM): delegated tasks have no
   claim → progress → submit-for-acceptance cycle; agents can only flip
   `done`. Proposed loop runs entirely through the existing review gate and
   feeds trust scores for free.
2. **The CLI can see but not act** (D1 + D3/D4 fixes): My Day in the
   terminal lists open questions and pending reviews the CLI cannot answer
   or approve. All REST endpoints exist; wrappers missing. Directly attacks
   the >50% off-web adoption bar.
3. **The manager's week has no cockpit** (P1 + C1): Monday planning and
   Friday close-out are assembled by hand from five pages; both agents
   proposed the aggregate view / ritual jobs that end that.

---

## Chief of Staff — ranked by manager attention saved per week

| # | Proposal | Mechanism (deterministic) | Effort |
|---|---|---|---|
| C1 | **Week-Open / Week-Close ritual** | Two JOBS-registry jobs: Friday close-out sweep (due commitments to disposition, aging answered questions, stuck `closing` engagements, stale proposals → attention items + artifact packet); Monday manager brief (the manager's OWN obligations: commitments, decisions past review_by, questions assigned, external promises due, 1:1s without a prep-brief read) | M |
| C2 | **Received-promise chaser** | `commitments.direction ('given'\|'received')` + `last_nudged_at` (migration); capture grammar `awaiting: <who> — <what> by <date>`; hourly rule nudges the creator, escalates to manager after 2 silent cycles; `waiting_on: commitment:N` already works | S/M |
| C3 | **Meeting outcome loop + recurring-meeting audit** | `events` gains agenda/engagement_id/outcome_status; post-meeting attention item deep-links to /ingest (pipeline already exists); weekly finding: recurring meeting with 0 captured outcomes 3 weeks running → "cancel, shrink, or make async" with hours-burned receipt | M |
| C4 | **Stakeholder open-threads brief** | Read-only union over commitments.to_whom / intake.requester / questions.asked_by / events.attendees for non-team names; morning rule attaches it to external-attendee meetings | S |
| C5 | **Decision→engagement links + cascade** | `decision_links` table; populated at record time + by scanning refs; consumed by scoped context packs, supersede/stale notifications to leads, handoffs | M |

CoS fixes (1-4): **applied** in `6534e96` (answer/resolve/disposition/close
notification loops).

## Product Manager — ranked for a 5-person team's first 90 days

| # | Proposal | Core | Effort | Metric |
|---|---|---|---|---|
| P1 | **Weekly Planning Cockpit** | `GET /api/planning` aggregate + one page in meeting order: kept-% & carryover → capacity vs draft (conflicts inline) → intake queue → stale decisions → one-click commit. Pure composition of existing endpoints | S/M | kept-% ≥ 80% within 6 weeks |
| P2 | **Availability ledger** | `absences` table (pto/on-call/focus); capacity, what-if, plan draft, escalation routing, ICS all become absence-aware — a person on PTO is a 20% capacity swing the math currently ignores | M | zero committed tasks assigned to someone absent >50% of the week |
| P3 | **Shared 1:1 loop** | pairwise-visible agenda scope (`1:1:` capture prefix), auto carryover, action items become tasks; manager's private notes untouched | M | ≥70% of 1:1 actions closed by next session |
| P4 | **Interrupt ledger** | derived: task created after the week's line locked + done same week = unplanned; team-level ratio in flow/readout + findings rule. Zero user action | S | unplanned share co-reported with kept-% |
| P5 | **Standup auto-draft** | `strands standup --draft` prefills from your own activity trail (trailer-closed tasks, captures, blockers); own-data-to-self only | S | standup participation ≥80% |

PM fix-class items (NOT applied — need decisions): TZ hardcoded UTC for all
human rhythms (needs `STRANDS_TZ`); OIDC↔key identity bridge undefined
before deployment; FEATURES.md anti-surveillance claim vs per-person WIP on
/portfolio (re-scope claim or aggregate display); `promised:` audience
ambiguity; CLI naming split in docs; Slack fb: documented refusal (verified
in code: capture requires strong auth — fail-closed, doc-only gap);
onboarding key step says self-serve but needs an admin (copy already points
at bootstrap; could be clearer).

## AI Engineer — agent layer, ranked usefulness-per-risk

| # | Proposal | Safety story | Effort |
|---|---|---|---|
| A1 | **Delegation work loop** — claim_delegated_task / report_progress (worklog) / submit_for_acceptance | completion is a proposal reviewed by the mandatory sponsor; verdicts feed trust scores; works keyless | M |
| A2 | **System-filed authority promotion/demotion proposals** | weekly job scans trust_scores → files pending_changes (promote review→notify→autonomous, demote on rejection streaks); agents can't approve anything, so no self-promotion | S |
| A3 | **Gated agent morning sweep** | new `nudge` registry entity (apply = notify); daily rules over existing reads file ≤5 proposals/day, deduped weekly like findings; cheapest authority on-ramp | M |
| A4 | **Agent-to-agent handoff** | `handoff_task` keeps sponsor immutable; hop itself is a proposal the sponsor approves | S/M |
| A5 | **Proposal bundles** | bundle_id/seq on pending_changes; symbolic refs ($1.id) resolved at apply; per-bundle approval with per-step untick; atomic apply | M/L |

Agent-layer fixes: #2 (phantom agents), #3 (cancel_event authority), #4
(capture rate limit) **applied** in `6534e96`. NOT applied (design): #1 chat
identity hardcoded `actor="agent"` — matrix/trust collapse to one identity
for the whole chat surface (needs a contextvar; prerequisite for personas);
#5 rejected proposals nag agent inboxes forever (acked_at column); #6 trust
streak display + no demotion + skips notify rung; #7 notify-tier writes
link to empty /review; #8 registry apply-signature assertion.

## Developer Advocate — ranked by daily friction removed

| # | Proposal | Sketch | Effort |
|---|---|---|---|
| D1 | **`skein inbox` / answer / review** | `skein answer 14 "..."`, `skein review show/approve/reject`, `--all-from <agent>`; pure REST wrappers; converts the CLI from "capture and look" to "close the loop" | M |
| D2 | **Attention count in the shell prompt** | `skein attention --porcelain` reads a 60s-TTL 0600 cache, never blocks, never errors; `skein install-prompt` writes the starship/PS1 snippet (install-hooks precedent) | S |
| D3 | **Branch-aware git flow** | `skein task start 42` → branch `task/42-slug` + in_progress; prepare-commit-msg hook injects trailer from branch; `skein pr-body` composes task + engagement pack + commits for `gh pr create` | M |
| D4 | **MCP mid-task parity** | add update_task / answer_question / resolve_blocker / list_* / ask / week via gated_write; deliberately NOT review-approval over MCP (agents must not launder their own proposals) | S/M |
| D5 | **Offline capture outbox** | JSONL outbox + idempotency key, auto-flush on any successful command; `my-day --cached` | S/M |

DX fixes: F1 (docstring dead-end) + F2 (STRANDS_API_URL alias) **applied**.
NOT applied: F3/F4 (CLI act-parity = feature D1), F6 (arg grammar
normalization), F7 (`context --engagement`), F8 (`skein ask`).

## Whimsy Injector — ranked by delight-per-effort

| # | Proposal | Effort | Staleness risk |
|---|---|---|---|
| W1 | **The Skein Takes Flight** — one goose per ship this season forms a V on Team Pulse (a flock of geese in flight is literally called a *skein*); count-based, team-level, resets each season | S | low |
| W2 | **Onboarding goose takeoff** — the completion moment is currently a silent no-op; show the card once more with the goose lifting off + "Warped, threaded, and flying." | S | none (one-shot) |
| W3 | **Dye-lot season names** — deterministic natural-dye names per season index ("the Season of Woad"); season-close ritual line | S | low |
| W4 | **`honk`** — ⌘K easter egg: one goose glides across the viewport, nothing persisted, reduced-motion respected | S | medium |
| W5 | **The Bolt** — selvage permanently accumulates one repeat per ship (offset derived from ship count; hover: "31 repeats woven") | M | very low (improves with age) |
| W6 | **Epitaph pool** for blocker funerals, seeded by blocker id; team can PR new lines | S | medium |
| W7 | **Loose Threads** — woven 404/error pages (frayed thread CSS + seeded line) | S | low |

Whimsy fixes: 1 (hourly loading seed), 2 (selvage snap-back), 3 (dead chat
pool), 5 (funeral coda), 6 (confetti key), 7 (UTC joke rollover), 8 (stray
emoji) **applied** in `6534e96`. Not applied: 4 (wave hydration — verified
current memoized design is sound; left as-is).

---

## Assistant recommendation (for triage)

- Cheapest high-yield trio: **D2 + P5 + W2** (all S — a day total).
- Highest-leverage structural pair: **A1 + D1** (delegation loop + terminal
  review — completes "agents as teammates, humans in control").
- Brand move: **W1** (small, deterministic, makes the product name land).
- Do the **chat-identity contextvar fix** before any persona/multi-agent
  work — everything per-agent (authority, trust) depends on it.
- Defer: A5 (bundles) until simpler pieces prove out; P3 needs care (new
  visibility scope).
