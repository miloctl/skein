# Whole-app fresh-eyes audit — 2026-07-27

Post-review of the toast/PersonInput commit (1 bug, fixed same-day: mouse-picked
datalist suggestions now commit) plus a full audit. Live sweep: 12 routes 200,
zero console errors, profile toast verified <150ms.

## Findings awaiting triage (verified, file:line in transcript)

- W1 M Tasks/milestones: create-only in UI (no edit/delete; unbounded lists) — fix now
- W2 S Chat SSE never sends the personal API key (401s on token-locked deploys) — fix now
- W3 M Work-graph linkage set-once + silent NULL on name mismatch; engagement rename impossible — rethink
- W4 S Allocations append-only; capacity ignores date windows (disagrees with conflicts) — fix now
- W5 S Agent memories undeletable yet injected into every prompt — fix now
- W6 S ?as= persona param resurrects after dismissal on every thread switch — fix now
- W7 S My Day "due within a week" shows the whole team's deadlines as yours — fix now
- W8 S GET /api/admin/keys + POST /api/admin/backup are weak-identity vs documented model — fix now
- W9 S Daily digest filed as a note daily → buries the KB; notes route ignores keyword filter — fix now
- W10 S Event cancel is agent-only (no REST parity); deleted events haunt FTS — fix now
- W11 S People page: no last-request-wins guard on private notes (cross-person flash) — fix now
- W12 S Pulse ballot-stuffable (reload revotes); unbounded write payload lengths — plan

Smaller: approve loses proposing-agent authorship in created_by; prompt()/alert()
stragglers (settings rename/deactivate, sidebar delete confirms); timeAgo only on
agents page; intake accepts empty titles; answer overwrite w/o guard; ingest
file-as collides on duplicate lines; raw SQL in two routes vs CLAUDE.md rule.

Verdict: invariant layer solid; the app creates records far better than it
corrects them. Recommended order: W2+W5+W6+W7 (pre-first-week, all S), then
W1+W4+W8+W9+W10+W11, then W3 (design) and W12 + smaller.
