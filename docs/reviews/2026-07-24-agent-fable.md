# Independent review — agent-fable (2026-07-24)

*Saved verbatim. Second of two independent reviews commissioned 2026-07-24;
see the companion file for agent-sol's review.*

---

This is a genuinely well-designed system — the deterministic-core-with-optional-LLM bet, provenance on every write, the review gate, and especially the anti-surveillance rule enforced at the service layer are choices most teams get wrong. So I'll skip the "have you considered adding tasks" tier and go straight to what I think moves it forward, organized by theme. Throughout, I've tried to respect your design grammar: every idea should have a deterministic core, with LLM as garnish.

## 1. The missing layer: people management

Skein is excellent at managing *work* and thin on managing *people* — which is notable given the job you're about to start. The portfolio layer exists; the manager layer doesn't.

- **1:1 workspace.** A rolling shared agenda per pair (either party adds items async between sessions), action items that become real tasks, and a "since last time" auto-brief assembled from data Skein already has: their standups, blockers, open questions assigned to them, commitments kept/missed. Deterministic core is just queries you've already written; an LLM can optionally summarize. The critical design constraint: hard-scoped visibility to the two participants, excluded from the context pack, findings, and insights entirely. This is the single feature that would most directly support your actual new job.
- **Private feedback journal.** A capture prefix like `fb: chen — great pushback in design review` that lands in an author-private journal keyed by person, plus a private-only nudge ("nothing captured for Dana in 3 weeks"). This directly operationalizes "small, fast, frequent feedback." It lives inside your anti-surveillance rule if it's author-private and never aggregated — worth documenting that boundary explicitly since it's the first person-keyed record in the system.
- **Skills & growth interests feeding what-if staffing.** Self-declared skills/interests per person, so staffing projections can flag growth fit, not just capacity: "this prototype needs RAG work; Chen listed that as a growth area." Person-level data used to plan the future — squarely inside your own stated rule, and it's the mechanism that prevents interesting work from pooling with seniors.
- **A `manager_onboarding` playbook.** You have a playbook engine; write one that instantiates the listening tour as tasks, a month-2 "share back what I learned" milestone, and the ramp rituals. Dogfooding, and honestly a nice artifact of the transition itself.

## 2. Make it AI/ML-native

- **Experiment as a first-class entity.** Hypothesis, timebox, kill criteria, outcome (`validated / invalidated / inconclusive`). Closing one auto-drafts a lesson; the slip forecast treats experiments differently from feature milestones, because *an invalidated experiment that finished on time is a success, not a slip*. Add a findings rule for experiments past their timebox with no recorded outcome. Right now Skein structurally assumes all work is delivery work, which is the wrong shape for an ML team — and this encodes exactly the "timeboxed experiments, not deadlines" framing into the tool your leadership will see.
- **Token budget control loop.** You track spend; add per-agent and per-engagement budgets with soft alerts, auto-downgrade to a cheaper provider on breach, and a findings rule for anomalous burn. Spend observation → spend governance.
- **Golden-trace evals for the agents themselves.** You replay the capture classifier; extend the pattern to the Chief-of-Staff and planner: a scenario suite of prompts with expected tool-call traces, run in CI, failing on regression. Prompts and playbooks become code with gates, which they effectively already are.

## 3. Deepen the trust flywheel — this is your moat

The authority matrix + trust scores + review inbox loop is the most differentiated thing in the platform. Ideas to compound it:

- **Shadow authority level.** A rung between `forbidden` and `review`: the agent's would-be proposals are logged but never enter the inbox; a small weekly sample surfaces for grading, and grades feed trust scores. You get calibration data on a new agent or model with *zero* review burden, and promotion to `review` happens on evidence instead of vibes. This also makes model swaps safe to evaluate in production.
- **Authority with a half-life.** You already built the machinery — decisions carry `review_by` and go stale. Apply it to authority grants: `autonomous` and `notify` expire back to `review` unless reconfirmed. It makes agent promotion reversible-by-default and gives you a beautiful symmetry in the safety story: *nothing in Skein is trusted forever, not decisions, not agents.*
- **Structured rejection reasons.** A small taxonomy on reject (wrong entity, wrong content, right-idea-wrong-time, duplicate, tone) alongside free text. Review analytics get sharper, and the eval corpus becomes dramatically more trainable than prose-only rejections.
- **Reviewer ergonomics.** Diff views for mutations, batch-approve for similar items. Review latency is the tax on the whole flywheel; anything that lowers it raises how much authority humans are willing to delegate.

## 4. Close loops you've already opened

- **Findings efficacy tracking.** One-click "convert finding → task/question" with a link back, then track acted-on rate per rule and auto-quiet rules below a threshold. The findings engine starts auditing itself — very in keeping with "silence is a valid output."
- **Meeting-notes ingestion.** Paste raw notes or a transcript; a deterministic line-wise pass applies your existing capture grammar plus heuristics, an optional LLM pass catches the rest, and *everything lands as proposals in the review inbox*, never direct writes. This is probably the single biggest daily-convenience win available, and it slots perfectly into the review gate you already built.
- **Incident timeline auto-draft.** For `incident`-class engagements, assemble the postmortem timeline from the activity log — you already have provenance and timestamps on every write. Human edits, then it archives as an artifact. Spiritual sibling of the blocker funeral.
- **Waiting-on edges.** Minimal dependency links (task→task, milestone→external commitment) that feed slip forecast and health receipts. Deliberately *not* Gantt — just "what is this stuck behind," which is the 20% of dependency management worth having.
- **Risk register on the decision chassis.** Risks with owner, mitigation, and `review_by`, riding the same stale sweep. You've already built the hard part.

## 5. Surfaces

- **Per-engagement context packs** — scoped subsets of the org-brain so a delegated agent gets focused context; cheaper tokens, less noise, and cleaner blast radius.
- **Stakeholder status links** — expiring read-only page per engagement: health, kept commitments, next milestone, sanitized. Kills "any update?" pings and pairs naturally with the commitment ledger, since those are promises to exactly these people.
- **`/ask` with receipts** — Q&A over KB + decisions + context pack where every answer cites row IDs, findings-style. Keyless fallback is just FTS snippets, so it degrades honestly.
- **ICS feed** of events and due dates — an hour of work, disproportionate quality-of-life.

## One honest caution, wearing my earlier hat

Skein is exactly the kind of IC work the player-coach trap is made of: deeply satisfying, technically rich, and it *feels* like management work because it's about management. When the new role gets hard around month two, building Skein will be the most tempting retreat available, with the added camouflage of being "for the team." Two mitigations: prioritize the features that pay *you* back manager-time in the first 90 days (1:1 brief, meeting ingestion, exec readout you already have), and run your own big builds as experiments with timeboxes and kill criteria — dogfood the entity from section 2. Also, be gentle about introducing a homegrown platform to a team you just met; that's a month-3 move introduced through value ("here's the readout Skein generated") rather than a month-1 mandate, or it reads as the new manager's pet system arriving before the new manager's listening did.

If I had to pick three to build next: meeting ingestion (daily leverage), the 1:1 workspace (the job you're stepping into), and shadow mode (compounds your moat). Want me to spec any of these out properly — data model, endpoints, findings rules, the works?
