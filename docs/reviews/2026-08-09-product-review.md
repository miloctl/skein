# Skein — Product Strategy Review

**Shipped from this review on 2026-08-09:** R1 (the dropped-payload renders),
R2 (the artifact reader, as Work → Reports) and R7 (the lessons browser),
alongside the bounded-input census ratchet. R3, R4, R5 and R6 stay open and
live in `docs/ROADMAP.md`.

*2026-08-09 · grounded in the shipped code: 55 backend services, ~140 REST endpoints, 56 gated agent tools, 17 frontend surfaces, 18 scheduled jobs, and the roadmap's own self-diagnosis. This transcript is the definition site for R1–R7, cited from `docs/ROADMAP.md`.*

---

## 1. Executive assessment

**What Skein is today.** Three layers of very different maturity share one repo. First, a deterministic operating core — work graph with waiting-on edges, decisions with half-lives, a promise ledger, blockers with impact-based escalation clocks, capacity and allocation math, a 19-rule findings engine, weekly rituals — that is unusually complete for a product this young. Second, an agent-governance layer — a per-(agent, entity) authority matrix, a single write gate across chat, MCP, and scheduled runs, gate-issued receipts, verdict-fed trust scores, and a hash-chained tamper-evident provenance ledger — that is genuinely novel. Third, a presentation layer that has repeatedly lagged the other two. The 2026-08-08 product-gap review diagnosed this correctly ("the backend ran ahead of its surfaces") and shipped most of the fixes; this review finds the residue.

**Who receives the most value.** The engineering manager, decisively. The Monday planning cockpit, portfolio health with receipts, intake triage with RICE scoring, the review queue, and the week rituals form a complete weekly operating loop. The developer gets a strong morning surface (My Day, grouped by the judgment required, every row with a reason) and real coordination exhaust (forge webhook, CI webhook, git trailers, derived standups) — but the terminal-side loop is half-built, and the roadmap admits it: no `skein ask`, no branch-aware flow, no prompt badge.

**Does it fulfill the original dev-and-manager intent?** For managers, yes. For developers, partially: value concentrates at 9am and at commit time, and thins out in the hours between, where developers actually live.

**What it feels like.** More than a system of record, not yet a team operating system. It is a coordination layer with a working governance kernel for agent labor. The OS claim is earned when the deterministic insight engine drives the agents' agenda and when playbooks close the loop from plan → execution → adherence → lesson. The parts all exist. The loops are not yet closed.

---

## 2. Developer assessment

**Current value**

- *What to work on, and why:* My Day groups attention by judgment type (Decide / Unblock / Promise / Review / Notice) and every row carries a "why you're seeing this" line. That reason line is rare in this product category and is the difference between an inbox and a to-do dump.
- *Exhaust, not data entry:* pushing `task/42-*` starts task 42; a merged PR closes it; a red default-branch CI run files a blocker and green resolves it; standup "yesterday" is prefilled from your own activity ledger; `blocked …` in a standup files a real blocker with an escalation clock. This is the strongest anti-"another place to update" story in the product and the thing to protect above all else.
- *Context on demand:* the task peek is one landing place for every task reference — status, waiting-on, worklog, forge link, delegation. `?`-ask returns snippets citing `entity #id`, never unsourced prose. Per-engagement context packs give a delegated agent (or a human) a scoped brief.

**Friction and missing needs**

- *The terminal gap.* D2 (attention count in the shell prompt), D3 (branch-aware `skein task start`), D5 (offline capture), F8 (`skein ask`) are all unshipped. Status changes are covered by the forge, but a question, decision, or waiting-on edge requires a context switch to the browser. The ⌘K palette is excellent and exists only there.
- *Waiting-on is one hop, and it points the wrong way.* A task shows what *it* waits on; nothing anywhere shows what waits on *you*. "If I finish this, what unblocks?" is computable today from `waiting_on` plus `blockers.task_id` and is computed nowhere. Without a downstream view, maintaining edges is pure ceremony for the person typing them — and unmaintained edges quietly rot every synthesis view built on them.
- *Personal interrupt cost is invisible.* The interrupt ledger shipped, but only as a team ratio in the cockpit. A developer never sees what unplanned work did to their own week.

**Unsurfaced opportunities**

- `briefing.needs_you` — five categorized lists (open questions, pending reviews, your blockers, intake, notifications) — is fetched by `app/page.tsx` and never rendered.
- `skein context --write AGENTS.md` makes Skein the context supplier to every other agent a developer runs. It is advertised only in Settings copy; it belongs in the field guide and the onboarding checklist as a headline act.

**Would a developer use it consistently?** Mornings, yes — My Day plus the tab-title badge earn the visit. Midday is the risk: if "what's stuck on me" and "what did finishing that unblock" are not one keystroke away in the terminal, usage decays to standup compliance. And when developer input decays, every manager view downstream of it starves.

---

## 3. Engineering-manager assessment

**Current value** — strongest in class for a tool this size.

- *What is happening:* engagement health is R/Y/G **with receipts** (never a bare color), health *movement* since last week (snapshots, not vibes), a slip forecast labeled as a heuristic with its basis shown and its own calibration reader, flow metrics with honest small-n discipline (verdicts withheld under n=8), capacity ahead with PTO folded in.
- *What should I do:* the findings engine is literally an intervention-recommendation system — receipts, dispositions, convert-to-work, per-rule follow-through stats. The planning cockpit's card order (last week's kept-% first, then draft, conflicts, triage, decisions) is a runbook for the Monday meeting, and the order is deliberately load-bearing.
- *Delegation without micromanagement, structurally:* sponsor-bound verdicts, readable worklogs, and the anti-surveillance rule enforced in the service layer — no person-keyed insight endpoint exists, flow metrics and readouts name no people. A manager cannot drift into surveillance even if tempted. That constraint is what buys honest standups, and it is a real moat.

**Friction and missing needs**

- *The synthesis outputs are second-class citizens.* The Monday brief, Friday close-out, and exec readout — the manager's flagship artifacts — render as raw markdown in a `<pre>`. The daily digest has no UI at all; it exists only as a file under `data/artifacts/`. The most manager-shaped material in the product looks like debug output.
- *The outward-facing frame is unbuilt.* Promises received (C2), meeting outcomes (C3), stakeholder open threads (C4) — the roadmap sequences these correctly as cockpit cards; nothing has shipped.
- *The cockpit discards detail it already fetched:* per-person weekly capacity (`capacity_ahead[].people`) arrives on the wire and only the over-100% and away names render.
- *Decisions cannot cascade* (C5): superseding a decision notifies nobody whose work depended on it.

**Would a manager depend on it?** For the Monday hour and the review queue — yes, today, and those beat a Jira-plus-spreadsheet workflow outright. But the dependency is fragile in exactly the way the developer story is: every synthesis view is only as trustworthy as edge freshness and standup compliance.

---

## 4. Human-and-AI coordination assessment

**Genuinely differentiated**

1. **The governed write path.** One service layer, three doors (REST, agent tools, MCP), one gate. Authority per (agent, entity) with four levels; default is `review`; `forbidden` always wins; irreversible verbs are always proposals; flock and consult writes are always proposals regardless of the matrix. And it is *proved*, not asserted — `test_gate_coverage.py` invokes all 56 tools and fails any write that leaves no receipt.
2. **Receipts versus claims.** The UI states what happened from the gate's receipt box — "Queued for review #N", "Wrote task #12", "Refused", "Filed nothing" — never trusting model prose. This kills the agent-said-it-did-something failure class that makes teams stop trusting agent tools.
3. **Provenance as an input, not just an audit trail.** Review verdicts feed trust scores; streaks feed a Monday job that *proposes* authority promotions and demotions; agents cannot review, so there is no self-promotion path; `agent_verified` is stamped in exactly one place. Underneath, a hash-chained activity ledger with off-box anchors.
4. **Skein as context supplier.** Versioned org-brain packs, per-crew and per-engagement scoped packs, `skein context --write AGENTS.md`. This positions Skein as the memory for every agent the team runs, not only its own — the least tracker-like idea in the product.
5. **Keyless parity.** Everything above works with zero API keys. Mock is a mode, not a demo.

**Currently superficial**

- **The trust flywheel has no flow.** The review gate is optional, the unattended runner allowlist ships empty, and `trust_blocked` openly admits that in most deployments no streak can ever form. The moat is built and dry. Until at least one deployment runs the loop for real, the governance layer reads as ceremony.
- **The bench is broader than deep.** Ten personas, two flocks, an SVG trace diamond — charming, and the identity/gating design underneath is careful — but persona breadth does not compound the way governance does. Each new persona adds surface without adding loop.
- **The Chief of Staff is a clerk, not a chief of staff.** All proactivity lives in deterministic jobs (correctly — keyless-first). But the chat agent and the findings engine do not know about each other: no tool exposes findings, attention, or health movement, so a freeform "what should worry me this week?" cannot reach the system's own best answer. The deterministic `/briefing` command exists; the agent's tool loop cannot get there.

**Playbooks** are closer to executable operating procedures than the team may be giving them credit for: every milestone description is a "Done when: …" exit criterion, go/no-go checkpoints instantiate as real calendar events, lessons from the same class attach at kickoff. Missing: any UI at all (they are chat-only — `/plan`, `/playbooks`), and the closing half — nothing compares the instantiated plan against what actually happened, so adherence and plan quality never feed back into the YAML.

---

## 5. Surfaced versus unsurfaced value

| Capability | What exists | What users see | Derivable but unbuilt | Who benefits | Surface as |
|---|---|---|---|---|---|
| Attention & priority | `briefing.py`, judgment groups, reasons | My Day + nav badge + tab title | the dropped `needs_you` payload; "what unblocks if I finish X" | developers | downstream line in task peek + My Day |
| Status synthesis | digest, week rituals, exec readout — all computed daily/weekly | digest invisible; rituals and readout as `<pre>` dumps | a real artifact reader | managers | Artifacts tab under Work |
| Dependency & blocker detection | `waiting_on` edges, `blockers.task_id`, escalation clocks, CI auto-blockers | one text row in the peek | transitive blocked-set; "top unblocking move this week" | both | cockpit card + peek section |
| Decision tracking | full lifecycle: half-life, stale sweep, supersede chains, charter category | `/charter` is a genuinely good decision-log UI | decision→work links (C5); cascade on supersede | managers | link table populated at record time |
| Stale-work detection | graduated SLA ladder (3d→7d→14d), findings, flow | good — receipts everywhere | per-person nagging is *deliberately* absent (anti-surveillance) | — | leave as is |
| Intervention recommendations | findings + dispositions + convert-to-work | `/insights` only | findings in chat; findings in the runner's wake prompt | both | read-only `get_findings` tool |
| Role-specific briefings | week-open personal brief, My Day daily | notification + file artifact | a rendered personal Monday page | developers | My Day Monday variant |
| Workload & ownership | capacity, allocations, conflicts, what-if staffing | lists; per-person weekly detail fetched then dropped | the heat table is already on the wire | managers | render `capacity_ahead[].people` |
| Playbook adherence | instantiation rows, `completed_at`, forecast snapshots | nothing | planned-vs-actual at close; auto-drafted lesson from variance | team | close-out report on engagement close |
| Provenance-aware review | origin, trust scores, streaks, override flags | `/review` hides `origin`; trust lives on `/agents` | trust context at the verdict moment | reviewers | chip per proposal: "scout — 12/13 approved on tasks, streak 4" |
| Confidence & approval boundaries | authority matrix, ALWAYS_REVIEW, authority half-life | `/agents` prose that inverts with the gate | evidence pack (correctly deferred until a real promotion decision) | managers | adequate today |
| Project memory | handoffs, context packs, lessons | lessons are counted on the season band and browsable **nowhere**; search renders lesson hits as dead text | a lessons browser with class filter | everyone | Insights card or Browse section |
| Planned vs. actual | forecast calibration (shipped) | `/insights` card | playbook-level variance (above) | managers | with playbook close-out |

---

## 6. Prioritized recommendations

**R1 — Finish "deliver what is computed." Quick win.**
Problem: the client fetches data users then reconstruct by hand. Experience: render the dropped payloads — throughput chart on `/portfolio`, per-person capacity in the cockpit, RICE values prefilled on re-score, trust `current_level`, flock token costs on the diamond, `origin` chip in `/review`. Primary user: both. Impact: medium-high at near-zero cost. Complexity: low. Dependencies: none. Measure: adoption card + weekly pulse.

**R2 — An artifact reader. Quick win.**
Problem: the manager's flagship outputs (digest, Monday brief, Friday close, readout, handoffs) are files with an API (`GET /api/artifacts`) and no UI, or `<pre>` dumps. Experience: a rendered artifact list + reader under Work. Primary user: manager. Complexity: low. Measure: ritual buttons used weekly; readouts regenerated.

**R3 — Provenance at the verdict. Strategic differentiator.**
Problem: a reviewer judges every proposal blind; trust data lives two pages away. Experience: each `/review` row carries origin, the proposer's approval rate on that entity, current streak, and "one more approval suggests promotion" when true. This converts the queue from a chore into the trust flywheel's actual UI — the moment provenance stops being audit history and starts being decision support. Primary user: reviewer/manager. Complexity: low-medium; all data exists. Dependencies: none. Measure: review latency (already tracked); promotion proposals acted on.

**R4 — Downstream visibility. Foundational.**
Problem: `waiting_on` edges are write-only ceremony; nobody sees the reverse direction. Experience: "This task unblocks: …" in the peek and My Day; a "top unblocking move" line in the cockpit. Primary user: developers first, managers second. Complexity: medium (reverse closure over `waiting_on` + `blockers.task_id`). Measure: edge count and freshness rise after ship.

**R5 — Give the Chief of Staff the system's own eyes. Strategic differentiator.**
Problem: the deterministic insight engine and the conversational agent are strangers. Experience: read-only `get_findings` / `get_attention` tools plus a prompt line, so "what needs attention?" answers from the findings engine with receipts; later, the same tool seeds the unattended runner's wake prompt. Complexity: low — read-only, no new gate concerns. Measure: chat turns that cite findings; dispositions arriving via chat.

**R6 — Playbook close-out: planned vs. actual. Strategic differentiator.**
Problem: playbooks never improve; lessons are anecdotes. Experience: on engagement close, diff the instantiated plan against actuals (dates, added/removed tasks, skipped rituals) and auto-draft the lesson from the variance, reviewed like any proposal. Complexity: medium. Dependencies: none — all data exists. Measure: lessons per close; playbook YAML edits per quarter.

**R7 — The CLI arc. Foundational (developer retention).**
F8 `skein ask`, D2 prompt attention count, D3 branch-aware flow — the roadmap's own list, and its own admission that the web outran the terminal. Complexity: low each. Measure: the `/insights` adoption card's existing >50%-non-web target.

**R8 — Manager frame cards (C2, C3, C4). Foundational.**
Received-promise chaser, meeting outcome loop, stakeholder briefs — already specified in the roadmap, already sequenced correctly, land as cockpit cards. Nothing to add except: do them before any new agent-layer breadth.

**R9 — Bounded-input census. Foundational (safety).**
The roadmap calls it the highest safety value per hour in the file. Agreed — ~46 unmetered mutating routes and service-layer caps that disagree with route caps deserve the structural test before any new write surface ships.

**R10 — Unnecessary distractions.**
More personas or flocks; more theme packs and delight items (W1–W7 are fine later; not now); dependency graphs/Gantt (text receipts beat diagrams at this team scale — the refusal already in CLAUDE.md is correct); outbound forge sync and comment import (`origin` has no external-author value — the deferral is principled, keep it); an LLM "insights narrative" layer (the deterministic engine with receipts *is* the credibility; narration stays a garnish).

---

## 7. Final prioritization

**Three most important next:** R3 (provenance at the verdict), R1+R2 (surface the computed synthesis), R4 (downstream visibility). Each closes a loop that is already 80% built, and each strengthens the daily-use gravity the product needs more than it needs any new machinery.

**Strongest potential differentiator:** the governed agent-labor loop — authority matrix, one gate, receipts, verdict-fed trust, tamper-evident provenance — plus context packs. No mainstream tracker has an answer to "how does an agent earn autonomy, and how would we know?" Skein has the entire mechanism. What it needs is flow: R3, and one real deployment running the unattended runner behind its ceilings.

**Largest current product risk:** developer disengagement starving the synthesis. Every manager surface is derived from developer exhaust. Forge and CI cover status, but questions, decisions, and waiting-on edges are voluntary — and if the terminal loop stays unfinished and downstream value stays invisible, the edges rot and the cockpit quietly becomes fiction with receipts. The second risk is the dry flywheel: governance features no deployment exercises will read as ceremony, and ceremony gets deleted.

**Explicitly do not build:** outbound forge sync; comment import; graph visualizations; any fourth write path; more bench breadth; shadow authority (the review queue *is* shadow mode); person-level analytics of any kind — the anti-surveillance rule is a moat, not a gap.

**What would make Skein indispensable:** a Monday cockpit that is trustworthy with nobody having done data entry — status from the forge, blockers from CI, yesterday from the ledger, plans from playbooks, and agent work arriving as reviewable proposals with trust context right at the verdict. At that point, removing Skein means re-hiring a human chief of staff. The bones for all of it are already in this repo; what remains is mostly surfacing and flow, not new machinery.
