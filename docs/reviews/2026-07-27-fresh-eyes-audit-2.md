# Fresh-eyes audit №2 — 2026-07-27

> STATUS: CLOSED 2026-07-28 — all P1/P2 items and the recommended P3 scope
> fixed across commits 7d619be..d6eb029 (three phases, each two-agent
> reviewed); CLI verbs shipped in d6eb029. Consciously skipped: ratelimit
> MAX_KEYS got oldest-idle eviction (not the full rework); context-pack
> GET keeps its bootstrap publish (RuntimeError now a 400).

Five parallel auditors (backend services, REST surface, frontend, agent
layer, docs-vs-reality) over the whole app at HEAD `2588e9e`, plus a live
Chrome walk of every surface (all pages render, console clean, nothing
visibly broken). Recently-hardened areas (delegation loop, sponsor
verdicts, rituals, absences) were excluded — this pass is everything else.

Verdict up front: docs-to-code alignment is unusually good (every
endpoint, job, tool, and cap the docs claim actually exists), and the
recently-audited surfaces held. The findings below are the parts fresh
eyes hadn't hit yet.

## P1 — fix before real team use

1. **`remember` is a fully ungated agent write** (tools/memory.py:13,
   mcp_server.py:176). Memories inject into every future chat's system
   prompt; `about_user=""` surfaces for everyone. A prompt-injected agent
   plants persistent steering text in one call the kill switch cannot
   reach ("memory" isn't a registry entity, so `forbidden` can't even be
   set) — while *removing* a memory needs a human verdict. No rate or
   length cap either (also: memories table has no origin/created_by —
   services/memory.py + migration needed). Fix: registry entity
   `memory:create`, wrap both tools in gated_write, cap size/rate.
2. **A weak `X-User` header can claim any non-bench agent identity**
   (deps.py:25 → users.py:17). `ensure_user` only guards bench slugs;
   agents minted by delegation (`scout`, `research-bot`) resolve for any
   browser. Writes land as the agent with `origin=human` — bypassing the
   review gate while feeding that agent's attribution/trust/inbox. Fix:
   reject weak identities whose row is kind='agent'; also stop minting
   roster rows on GETs.
3. **Dashboard bricks on one transient fetch failure**
   (dashboard/page.tsx:239-250, 331-337). `load()` = Promise.all over 12
   endpoints after every mutation; any single blip sets a permanent
   full-page error (success never clears it). My Day already has the
   right pattern (banner + keep previous state).
4. **`setApiKey` doesn't notify same-tab subscribers**
   (lib/api.ts:29-33). After "Test & save" the Remove button never
   appears and the nav's strong-identity dot stays off; after Remove, the
   page says the key "is not working". Fix: dispatch the storage event
   like every other writer.
5. **Dispositioned intake requests can be resurrected by re-scoring**
   (intake.py:72-99). `score_request` has no status guard — a declined
   request flips back to `scored`, re-enters triage, and can be accepted
   a second time (double engagement). One-line guard.
6. **Free-text due dates corrupt calendars and due-soon logic**
   (commitments/work/engagements/schedule). No date validation on
   commitment/milestone/task/allocation dates; a `due_date="soon"`
   emits `DTSTART;VALUE=DATE:soon` which makes strict clients reject the
   ENTIRE ICS feed; string-compared dates silently vanish from every
   due-soon surface. The validator already exists in collab.py.
7. **Export/restore silently drops five newer tables** (admin.py:24-52):
   absences, task_worklog, finding_dispositions, job_outcomes,
   app_settings. A restore loses the PTO ledger and every delegation
   worklog with zero warning.
8. **1:1s page can show one person's private notes under another's name**
   (people/page.tsx:55-78). Notes aren't cleared on person switch; if the
   second fetch errors, the wrong person's private notes sit there
   indefinitely. Privacy-sensitive surface — clear on switch.

## P2 — bounds, consistency, dead-ends

9. **Chat is the least-protected biggest sink** (chat.py:33): no
   max_length on message, no rate cap — disk/tokens/transcript bloat.
10. **Create endpoints unbounded where their edit twins are capped**
    (api.py: NoteIn, BlockerIn, IntakeIn, CommitmentIn, TaskIn,
    MilestoneIn, QuestionIn, AnswerIn, DecisionIn, StandupIn, EventIn,
    EngagementIn, LessonIn, SupersedeIn, KeyIn.label, UserRenameIn…).
    You can create a 50 MB note you're not allowed to edit.
11. **No rate caps on content-creating writes**; digest/readout/handoff
    write files per call; POST /digest also inserts duplicate artifact
    rows (readout fixed this exact bug — copy its path-upsert).
12. **Unbounded lists**: questions, review pending (the growing one!),
    intake, blockers, engagements (with N+1), My Day pending_reviews
    (hottest page). Same LIMIT convention as siblings.
13. **404 vs 400 chaos for missing entities** — same failure maps to
    different codes per verb. Fix once: NotFound(ValueError) subclass +
    one handler.
14. **`rename_user` bypasses identity guards** (users.py:154): can merge
    a human into an agent row (folding trust/authority history across
    the boundary), take a bench-persona slug, and writes unclamped names.
15. **Blocker owner / decided_by / allocation person never
    roster-validated** — escalations notify "Mira " (trailing space), a
    user that never logs in; silent forever without Slack.
16. **`cancel_event` ignores earned authority** (tools/schedule.py:52):
    autonomous/notify grants still refused in review mode; can't be
    proposed either. Register `event_delete` in the registry and use
    gated_write (also settles the CORRECTIONS rule-2 overclaim).
17. **MCP agents dead-end on the delegation loop** (mcp_server.py): they
    see delegated tasks in my_inbox but have no claim/report/submit
    tools; complete_task's refusal names a tool that doesn't exist there.
18. **Empty-payload update proposals fail in the reviewer's face**
    (tools/work.py update_task/update_milestone, edit_commitment): guard
    like the sibling tools ("nothing to change") before proposing.
19. **Frontend drift batch**: portfolio swallows 5 of 6 load failures
    into lying empty states; no in-flight guards on people/charter/intake
    submits (held Enter files duplicates); dashboard load() lacks the
    generation-counter race guard its siblings have.
20. **Bearer middleware outside CORS** (main.py): with STRANDS_API_TOKEN
    set, 401s reach the browser as opaque CORS errors.

## P3 — polish

- Ship It recap counts only milestone-linked tasks (engagements.py:216)
  — contradicts the open-task check in the same function.
- Engagement update has no "-" clear sentinels (timebox_end/lead/… can
  never be emptied).
- slip_forecast uses updated_at as completion time — post-done edits
  poison the forecast (milestones need completed_at).
- publish_digest consumes the daily claim before building — a build
  failure burns the day.
- ICS: schedule_event accepts isoformat shapes (`space` separator,
  offsets) that _ics_dt then silently drops from the feed.
- Literal `’` rendered on the Agents page card title.
- Pulse-vote dedupe key isn't per-user (siblings are).
- Charter inputs unlabeled; supersede editor drops focus on Escape.
- Growth interests: write-only (no GET/prefill, can't clear, alert()).
- Dashboard calendar cutoff uses UTC date (hides today's evening events
  west of UTC).
- STATUS_COLORS/LEVEL_COLOR lookups without the Badge fallback render
  className "undefined".
- ratelimit MAX_KEYS lockout punishes the innocent next user.
- GET /context-pack publishes v1 as a side effect (non-idempotent read);
  RuntimeError there → 500.
- POST /week/plan task_ids uncapped (siblings cap at 100/200).
- Mock provider: persona masthead repeats every message; /remember reply
  promises injection mock never does.
- System prompt: no delegation-loop or absence guidance; says "Strands"
  where display surfaces say Skein.
- GET /api/agents/entities offers meaningless authority knobs
  (task_completion/authority/weekly_plan).
- Docs: seed.py demos none of the newer features (no commitment, absence,
  delegation, worklog, charter category, review_by); FEATURES omits the
  strong-verdict requirement for streaks and 3 of 14 jobs; CORRECTIONS
  rule 2 overclaims for events; README ⌘K list stale; INSIGHTS rule 16
  reads as built; CLI verbs SHIPPED (commitments/absences/review/worklog/inbox); lint.sh
  comment says mypy covers CLI (it doesn't).

## Explicitly verified clean

Chrome walk: all 11 surfaces render, zero console messages. Auditors:
gate coverage of all other mutating tools matches the registry exactly;
playbook YAMLs conform; chat SSE/thread ownership/key revocation/ICS
token compare sound; migrations/claim_job CAS sound; ingest mapping
complete; review-doc status headers accurate; first-run commands work as
written; seed runs green; 391 tests pass.
