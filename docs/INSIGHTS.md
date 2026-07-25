# Insights & findings engine — design spec

**Status: BUILT** (`services/insights.py`, `/insights` page, findings in the
digest and exec readout; migration 011). The panel's original gate (≥4 weeks
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

## The findings rules (16 rule IDs across 15 entries)

Machinery: finding = `{rule_id, severity, message, n, window, receipt}`;
receipt = row IDs + computed numbers JSON'd at fire time. Dedupe as built:
a rule fires at most once per (rule_id, subject, ISO week) — no re-fire
within a week even on severity change.

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
15. **Authority stale** — an `autonomous`/`notify` grant past its `review_by` (set to grant+90d; NULL falls back to `updated_at`+90d). The half-life as a nudge, not a demotion state machine: reconfirm by re-granting, or demote. `forbidden`/`review` never expire — the kill switch is forever. Subject = agent+entity; n=1 fires.

(16. Forecast miscalibration — quarterly, once `forecast_snapshots` has n≥8 completed milestones.)

**Dispositions** close the loop on findings: dismissed / deferred / converted
/ resolved, keyed on `(rule_id, subject)` because findings re-fire weekly as
new rows. `dismissed` suppresses the (rule, subject) for 28 days; `deferred`
until its date; `resolved`/`converted` never suppress — a re-fire after a fix
is signal. Dispositioned findings leave the digest. Per-rule follow-through
(fired / acted-on / dismissed) surfaces on /insights; rules nobody acts on
get retired at season end by the maintainer, not by an auto-quiet loop.
