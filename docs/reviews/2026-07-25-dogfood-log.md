# Skein dogfood log — simulated 3-week team arc
Team: claude (lead), mira (eng), tomas (eng), scout (AI agent)
Format: [friction|bug|idea|ok] — observation
- [friction] `q: tomas — question` does NOT assign to tomas; digest then prints "→ unassigned" next to a question that names its assignee. fb: already parses `person —`; q: should too (only when person matches a known user).
- [friction] `decision: ... review by 2026-10-01` keeps the date in the title; review_by stays NULL so the half-life sweep never fires. Capture should parse a trailing `review by YYYY-MM-DD`.
- [ok] `promised: ... (external)` audience parsed; standup blocker auto-filed; ingest classified 4/6 lines and refused the noise.
- [ok] Monday jobs all idempotent-ran: sweep escalated the week-old blocker, weekly plan filed as proposal, findings deduped per (rule,subject,week).
- [idea] digest leads with job_stale (artifact of time shift here, but real deploys: 3 red lines about infra before any team content — consider grouping job_stale into one line).
- [friction] No way to (re)assign an existing question — only POST /questions (with assignee) and /answer exist. A question captured without an assignee is stuck unassigned forever unless someone answers it out of the blue. Need PATCH /api/questions/{id} (assigned_to) + service + tool parity.
- [friction] POST /api/feedback pulse vote requires input_text (422 without it) — but a pulse vote HAS no input text; the My Day buttons must be sending a filler. API should make input_text optional for kind=pulse.
- [ok] /ask works with matching words and cites question #1 incl. the answer snippet; multi-word natural phrasing ("what latency number...") returns nothing because FTS ANDs every token. Note says "try different words" which is honest. [idea] OR-fallback when AND yields zero.
- [idea] create_task tool/payload has no engagement link (only milestone_id); asked the agent for "a task on the Alerting rules engagement" and the linkage was silently dropped. Consider engagement_id on tasks or auto-milestone.
- [ok] Ollama chat agent -> create_task -> review gate -> pending proposal #7 with clean payload incl assignee+due. Gate held; agent said "queued for review" correctly this time (prompt fix from live test held).
- [friction] Any X-User value silently creates a user row ("x" now shows in /api/users, adoption WAU, team_humans). Typos become phantom teammates and there is no deactivate/merge. Need: users PATCH active=0 (and UI), or at least stop counting never-authored users.
- [ok] closes require conclusions (route is PATCH status=closed); experiment close auto-drafted a lesson; trust table suggests nothing at streak 2 (correct, threshold 5); review stats + adoption + flow all real numbers.
- [bug] /insights findings feed: list_findings() never joins finding_dispositions -> dismissed/deferred findings render identical to open ones; users can re-dismiss with no feedback. Fix: LEFT JOIN latest disposition, badge in UI, dim acted-on rows.
- [friction] intake accept creates an engagement with no lead ("lead unset" on portfolio + context pack); accept dialog should ask for one.
- [ok] insights page: follow-through flags job_stale "retire candidate?", MTTR honest below n=8, adoption surface split, automation ratio co-presented with rejection rate — all as designed.
- [bug] Approving/rejecting a proposal leaves its "Review needed: #N" notification unread forever — briefing shows stale review asks after the review is done. Fix: mark matching notifications read on review resolution.

## FIX BATCH (this session)
1. capture: `q: <known person> — text` assigns + notifies (person must match a user; else leave in text)
2. capture: `decision: ... review by YYYY-MM-DD` -> review_by parsed + stripped (makes eval corpus green)
3. PATCH /api/questions/{id} (assign) + service
4. review approve/reject marks its "Review needed" notification read
5. findings list joins dispositions; insights UI badges dismissed/deferred/converted/resolved + hides buttons
6. feedback kind=pulse: input_text optional
7. users: deactivate endpoint + inactive excluded from roster/adoption/context pack
8. intake accept: optional lead field (backend + intake UI input)
9. digest: collapse N job_stale findings into one line
DEFERRED to PLAN.md backlog: task->engagement direct link for agent tasks; /ask OR-fallback on zero hits; user merge/rename.
