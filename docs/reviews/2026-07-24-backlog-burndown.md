# Backlog burn-down — full-project architecture review (2026-07-24)

Status: items 1–8 shipped 2026-07-24. Item 9 (runner isolation) stayed
open and lives in the roadmap's open backlog under "Ops". Moved out of
`docs/ROADMAP.md` in the 2026-08-03 consolidation.

Done in the same review cycle: milestone `engagement_id` link, unified
MCP/tool gate, handoff scoping, export coverage, indexes, allocations
provenance.

Items 1–8 shipped 2026-07-24 (backlog burn-down). The tests moved out of
`test_backlog.py` on 2026-08-02 into the file that names each behavior:
`test_db_transactions.py`, `test_engagements.py`, `test_migrations.py`,
`test_jobs.py`, `test_retention.py`, `test_turn_cost.py`, `test_digest.py`
and `test_flow_metrics.py`.

1. ~~`db.transaction()` context manager~~ — contextvar ambient connection in
   `db.py`; `playbooks.instantiate` and `intake.disposition_request` converted.
2. ~~Job registry~~ — `services/jobs.py` `JOBS` tuple drives cron + startup
   catch-ups; per-run outcomes in `job_outcomes` (migration 013); last-success
   + stale flag on `/health`; `job_stale` findings rule at 2× period.
3. ~~Name-join migration~~ — health/ship-it/handoff/forecast now join on
   `m.engagement_id`; `create_engagement` adopts orphan milestones created
   under the name before the engagement existed.
4. ~~Retention~~ — `services/retention.py`, monthly (1st, 04:00 UTC):
   forecast_snapshots >1y, read notifications >90d, job_runs/job_outcomes
   >90d. activity is the provenance ledger — kept forever.
5. ~~Staleness SLA constants~~ — `services/slas.py` (3d/7d/14d gradation).
6. ~~Extract `readout.py`~~ — composes portfolio + insights with top-level
   imports; the deferred-import workarounds are gone.
7. ~~`services/usage.py`~~ — chat token logging out of `routes/chat.py`;
   digest narrator inverted (`digest.set_narrator`, registered from
   `agents/narrator.py` at startup) so services never import the agent layer.
8. ~~MCP migration guard~~ — `db.pending_migrations()`; `mcp_server.main`
   exits instead of applying schema from a long-lived side process.
