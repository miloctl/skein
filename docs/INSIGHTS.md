# Insights & findings engine — design spec

**Status: BUILT** (`services/insights.py`, `/insights` page, findings in the
digest and exec readout). The panel's original gate (≥4 weeks
of usage) was overridden by the owner — small-n discipline makes early
emptiness honest: rules stay silent below their sample floors and the page
labels n everywhere. The trend-comparison rules (MTTR, rejection spike,
token anomaly) will produce their first verdicts once two comparable windows
of real usage exist. Panel review 2026-07-23 (PM + analytics agents).

## Principles

- **Findings first, page second.** Nobody visits a dashboard weekly; the
  digest and Monday plan are the pull. Findings are sentences with receipts;
  charts are the click-through evidence behind them.
- **Small-n discipline.** Medians over means everywhere; rolling 28-day
  windows vs. the prior 28; season windows for intake/engagements; n printed
  on every chart and finding; no %-change claim when either window has n<8.
- **Future vs. past rule (anti-surveillance).** Person-level data exists only
  for planning the future (capacity, what-if, personal nudges, My Day). Team
  aggregates only for judging the past. Enforced structurally: the insights
  service returns only team-rolled results — person-keyed endpoints must not
  exist. No leaderboards, ever (see Team pulse precedent).
- **One service layer.** Time-series/insights read through the same
  `services/` functions as /portfolio — no duplicate computations.
- Max **3 findings/week** in the digest, severity-ordered; silence is a valid
  output. Findings are recordable feedback (`kind=finding` in the eval
  corpus); a rule nobody acts on for a season gets retired.

## Metrics that made the cut

- Blocker MTTR: median + P85, rolling 28d vs prior 28d; split by impact only at n≥8/tier
- Review latency + rejection rate over time (extend `review_stats`, don't rebuild)
- Intake funnel conversion + time-in-stage at season/12-week windows
- Token spend by model/thread (sum, no small-n problem)
- Automation ratio (origin share of activity rows, monthly) — ALWAYS co-presented
  with rejection rate (Goodhart guard)
- Adoption (from `tool_usage`): weekly active users, surface mix, non-web share
  (>50% of captures outside the web UI = the DX success bar)

Cut as small-n noise: weekly throughput/cycle-time trend lines (use the
commitment kept-% and a rolling dot plot instead), question-response trend
(use an aging list), capture volume by kind, engagement duration trends
(all-time reference table with n, feeding slip forecast).

Better metrics to add: review queue aging · decision decay debt ·
slip-forecast calibration (median abs error from `forecast_snapshots`,
quarterly, n≥8) · weekly-plan edit rate · blocker source mix · escalation
rate · rejected-proposal themes · deferred-intake graveyard.

## The findings rules (26 rule IDs)

Machinery: finding = `{rule_id, severity, message, n, window, receipt}`;
receipt = row IDs + computed numbers JSON'd at fire time. Dedupe as built:
a rule fires at most once per (rule_id, subject, ISO week) — no re-fire
within a week even on severity change. `services/insights.py::RULES` is the
authority on what runs; this list explains each rule's why.

1. **MTTR regression** — median resolve, 28d vs prior 28d, ratio ≥1.5, both n≥8. Receipt: medians, ns, 3 slowest blockers.
2. **MTTR improvement** — ratio ≤0.67 (positive findings keep it from being an alarm system).
3. **Escalation spike** — ≥40% of resolved blockers escalated, n≥6, ≥1.5× prior.
4. **Aging WIP** — in_progress >14d ≥ max(4, 25% of WIP); point-in-time; no assignee grouping in the team finding.
5. **Commitment line slipping** — kept-% <60% (≥5 committed) or two weeks <75%.
6. **External commitment at risk** — open with due ≤7d, or flipped to missed; n=1 fires.
7. **Review queue stall** — ≥3 pending >72h, or oldest >7d.
8. **Rejection spike** — agent rejection ≥30%, ≥10 verdicts, ≥1.5× prior; receipt: review_notes verbatim.
9. **Intake stall** — median submitted→disposition >7d (6wk, n≥5) or ≥3 undispositioned >14d.
10. **Question aging** — any open >5d; digest names the question, only the private nudge names the assignee.
11. **Decision decay** — stale ≥3 or ≥25% of non-superseded corpus.
12. **Token spend anomaly** — weekly ≥2× median of prior 4 weeks AND above an absolute floor.
13. **Job stale** — a registered scheduled job with no successful run within 2× its period (from `job_outcomes`); never fires on a fresh install (needs ≥1 recorded attempt older than the threshold); subject = job name.
14. **Experiment overdue** — an open experiment engagement past its `timebox_end` with no recorded conclusion; conclude it or extend the timebox on purpose (`PATCH /api/engagements/{id}` accepts `timebox_end`). Subject = engagement id; n=1 fires.
15. **Authority stale** — an `autonomous` or `notify` grant past its `review_by` (set to grant+90d. NULL falls back to `updated_at`+90d). The configured grant stays on the audit row, but its effective level becomes `review`. Generic agent writes wait for a human even when the deployment review gate is off. Reconfirm by re-granting, or demote. `forbidden` and `review` do not expire. A sponsor's task delegation remains task-specific authority, while `forbidden` still stops that loop. Subject = agent+entity; n=1 fires.

16. **Feature unadopted** — a field-guide card (`backend/fieldguide/knots.yaml`) 30+ days past its `since` date that NOBODY on the team has tied — broken entry point or unwanted feature. Zero-adoption only, feature-keyed, nameless: when the count is zero no individual is singled out, which is what keeps this rule on the right side of the anti-surveillance principle (partial adoption never fires and never names). Sweeps unlock detection for all active humans first, so it doesn't fire on stale lazy state. Severity low; subject = knot id.

17. **Activity chain broken** — the provenance ledger disagrees with itself: a row was changed, removed, or written outside the chain. Walks the WHOLE chain, not the nightly tail — an anchor is a claim about the past, so incremental verification can never notice an edit to a row it already passed. The full walk also cross-checks the stored anchor, the append-owned live tip, and the unchained baseline, so it is strictly stronger than the tail run, not a different view of it. Reports the FIRST break only and stops: every later link is computed from a value already known to be wrong, so a second break downstream stays hidden until the first is repaired. When the walk passes, the rule also replays the **anchor log** (`backups/activity-anchors.log`, appended to the configured mirror nightly) — the check the in-DB marks cannot make: a whole-chain re-forge that rewrites `app_settings` too walks clean, but every anchored row's digest changed with the rewrite, so it no longer matches the line recorded the night it was verified. Severity high; subject = `seq:<n>` (or `unchained`, or `anchor:<n>` for an anchor-log mismatch), so a break re-fires every week until it is dispositioned. Receipt: the broken seq + the reason.

    A consequence worth knowing before writing a migration: **no migration may UPDATE or DELETE `activity` rows that carry a seq.** A pre-baseline migration (`pulse_anonymize`) rewrote `activity.actor` in bulk before the chain existed; the same migration written today would break the chain permanently at its earliest touched row, with no re-baseline path other than dispositioning the finding into silence. `tests/test_migrations.py` pins the refusal.

18. **Budget** — off until `SKEIN_MONTHLY_BUDGET_USD` is set. High when month-to-date estimated spend (from `SKEIN_MODEL_PRICES`, computed at write time) reaches the ceiling, with the top engagements in the receipt via the thread→engagement link. Medium "budget cannot be measured" when the budget is set but no call this month has a priced model — silence there would read as under budget while nothing was being counted. Unpriced calls are named in the message, never folded into the sum. Subject = `month:<YYYY-MM>` (or `unmeasured:<YYYY-MM>`).

19. **Turn runaway** — ONE agent turn that looped: cycle count in a single turn at or past an absolute threshold (`TURN_CYCLE_ALARM`, 25). Absolute, not a ratio — no honest baseline for "normal cycles" exists until a deployment has months of turns, and a ratio over a tiny sample fires on the second turn ever. The weekly token rule sees a spend trend; it cannot see one agent that burned a hundred cycles in an afternoon, which is the failure an unattended run makes expensive. Severity medium; subject = `turns:<usage_log id>`.

20. **Flock member failing** — a bench persona that failed EVERY time it was called inside a flock in the last 7 days (n≥2). `flock_traces` recorded a per-member status since flocks shipped and nothing read it, so a persona that always fails looked, from every surface, like a persona nobody uses. Every-call failure only: a persona that sometimes fails is a slow model, and a rule that fires on that gets ignored. Severity medium; subject = `slugs:<csv>`.

21. **Ledger rows adopted** — an `adopt_unchained` receipt landed: the nightly job chained rows that were written outside the chain and lowered the baseline. Adoption heals the chain instead of alarming forever; this finding is the push signal that replaces the permanent alarm. An adoption that no 'activity chain append failed' warning in the server log explains is the tamper signal. Severity medium; subject = `adopt:<seq>`, so each adoption fires exactly once.

22. **Meeting with no outcome** — a recurring meeting (grouped by title) whose instances all sit at `outcome_status` `pending` or `none` across `OUTCOME_SILENT_WEEKS` (3) weeks, with at least that many instances. `none` counts HERE and only here: answering "nothing came out of it" clears the daily ask on My Day, and it is the exact fact this rule totals up — filtering it out would let a series escape by admitting every week that it produced nothing.

    Two guards make the number and the subject defensible. **The receipt is computed per instance in Python, never as a SQL aggregate**: a date-only `starts_at` is an all-day block whose real length nobody recorded, so it contributes zero hours rather than 24, and the attendee count comes from each instance's own list rather than `MAX()` over the series. When no instance was timed the hours clause is dropped from the message entirely — "at least 0.0 attendee-hours" is a worse argument than the instance count alone, and a guessed duration is how a receipt stops being checkable. **A title carrying any roster name is skipped**, not anonymized. A 1:1 is both the meeting most likely to produce no recordable outcome and the one whose title is two people's names; a redacted one is still identifiable from its hours and cadence. Severity medium; subject = `meeting-<title>`.

23. **Interrupt load** — at or past `INTERRUPT_SHARE_ALARM` (50%) of a week's finished work was never planned before that week, n≥8 (`portfolio.py::VERDICT_FLOOR_N` returns `None` below the floor, and the rule short-circuits on it). The interrupt ledger shipped with the planning cockpit and nothing read it, so the number reached whoever opened that page on a Monday and nobody else. Team ratio, no per-person split. Severity medium; subject = the ISO week.

24. **Evidence gap** — a DELEGATED task accepted in the last 7 days with zero
worklog notes: the sponsor's verdict has nothing to audit. Scoped to delegated
work only, never every done task — judging each person's closing hygiene is a
per-person judgment on a team-wide surface, which the anti-surveillance
principle refuses, but a sponsor accepting an agent's work with no evidence is
the trust loop measuring itself. `delegated_agent` survives completion
(services/work.py clears it only on reassignment), so the completed row still
names the agent. This rule is also the demand probe for the deferred
evidence-pack spec (docs/reviews/2026-07-24-agent-sol.md): a season of its
firing rate is the evidence that spec waits for, and silence retires it at
season end like any other rule. Severity low; subject = `task-<id>`.

(25. PLANNED, not yet implemented: forecast miscalibration — quarterly, once `forecast_snapshots` has n≥8 completed milestones. The calibration *display* shipped on `/insights`; the rule that names a miscalibrated quarter did not.)

**Dispositions** close the loop on findings: dismissed / deferred / converted
/ resolved, keyed on `(rule_id, subject)` because findings re-fire weekly as
new rows. `dismissed` suppresses the (rule, subject) for 28 days; `deferred`
until its date; `resolved`/`converted` never suppress — a re-fire after a fix
is signal. Dispositioned findings leave the digest. Per-rule follow-through
(fired / acted-on / dismissed) surfaces on /insights; rules nobody acts on
get retired at season end by the maintainer, not by an auto-quiet loop.
