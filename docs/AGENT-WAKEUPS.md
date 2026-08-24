# Agent Wake Requests

## Objective

A human delegation starts one bounded agent turn. The delegation request does not wait for the model turn.

The agent reads its delegated inbox, claims work, records progress, and submits completed work for sponsor acceptance. The task panel shows the wake state.

Chat is a retry and follow-up surface. The task worklog, blocker register, and acceptance proposal remain the work record.

## Scope

A delegation with `origin=human` or `origin=agent_verified` queues a wake request. An autonomous agent delegation does not queue another agent turn.

The wake request starts one agent turn. It does not create an automatic continuation loop. The daily allowlisted runner can resume unfinished work.

A human delegation is the explicit trigger for the first turn. This turn does not require membership in `SKEIN_AGENT_RUNNER`.

The following controls still apply:

- A live model provider
- The task authority kill switch
- The daily token budget
- The wall-clock limit
- The review gate
- The task sponsor
- One turn per agent at one time, across the wake worker and the daily run
- `SKEIN_AGENT_WAKES_PER_DAY`, a workspace-wide daily cap on wake turns
  (default 24, 0 removes the cap).

## Non-goals

The first version does not:

- Retry a model turn after completion becomes unknown
- Run remote MCP tools
- Discover agents from the roster
- Continue an unfinished task without another explicit or scheduled wake
- Put the model reply in a human Chat thread
- Add a generic public queue API
- Add a second workflow engine.

## Data model

Migration `011_agent_wakeups.sql` adds one row per agent.

The row contains:

- `agent`
- `status`
- `requested_by`
- `trigger_task_id`
- `requested_at`
- `started_at`
- `finished_at`
- `attempts`
- `rerun_requested`
- `thread_id`
- `reason`.

Valid states are:

- `pending`
- `running`
- `completed`
- `refused`
- `failed`
- `completion_unknown`.

The row is an operational state record. The task activity row remains the provenance record for delegation.

## Enqueue contract

`delegation.delegate_task()` updates the task and queues the wake in one database transaction.

The queue uses an atomic PostgreSQL upsert:

- A terminal row becomes `pending`. `completion_unknown` is terminal: a new
  human delegation is a new explicit trigger, not an automatic retry.
- A `pending` row stays `pending` and records the newest trigger task.
- A `running` row stays `running` and sets `rerun_requested=1`.

The transaction registers `agent_wakeups.kick()` with `db.on_commit()`. A rollback drops the callback and the wake row.

`kick()` starts no worker when `SKEIN_SCHEDULER=0`. In this mode, the pending row remains visible.

## Worker contract

One process-local daemon worker drains pending wake rows. The worker uses a process lock to prevent a second drain loop.

The worker claims one agent row with `SELECT ... FOR UPDATE SKIP LOCKED`. The worker commits `running` before it calls the model.

A run reads the complete delegated inbox for that agent. Multiple pending delegations therefore coalesce into one turn.

If another delegation arrives during the turn, `rerun_requested=1` queues one more turn after a normal completion.

## Runner contract

The wake worker calls the existing bounded runner with an explicit wake key.

The explicit path:

- Bypasses `SKEIN_AGENT_RUNNER`
- Keeps provider checks
- Keeps task authority checks
- Keeps token and wall-clock limits
- Uses a wake-specific thread ID
- Uses the agent identity and background policy subject
- Records usage with the existing usage service.

The unattended agent receives a restricted tool set. Remote MCP tools, contributed tools, extra tools, planning, consultation, memory writes, and unrelated core writes are absent.

The allowed write tools are:

- `claim_delegated_task`
- `report_progress`
- `submit_for_acceptance`
- `raise_blocker`.

The allowed read tools cover the inbox, worklog, findings, attention, context pack, workspace search, artifacts, and existing coordination records.

## Crash contract

A pending row survives a process stop.

Startup recovery runs on every boot, with or without background jobs. A stale `running` row with `rerun_requested=1` becomes `pending`, because that flag carries a distinct human delegation that never got its turn. Every other stale `running` row becomes `completion_unknown`. The old process cannot still run after startup because the supported deployment uses one replica with `Recreate`.

Skein never retries `completion_unknown` automatically. A model turn can write before the process stops. An automatic retry can duplicate proposals, blockers, or worklog notes. A new human delegation re-pends the row.

A timeout also becomes `completion_unknown`. The existing model thread can remain alive after the wall-clock limit. Skein does not start another wake for that agent in the same process.

A failure before model invocation becomes `failed`. A provider, budget, or authority refusal becomes `refused`.

## Tool safety

The wake queue does not make existing tools idempotent. Its safety comes from one automatic invocation and no automatic retry after model invocation.

`claim_delegated_task` must lock the task row before it reads the current task status. This prevents two concurrent workers from claiming the same task.

`submit_for_acceptance` keeps its existing task lock and pending-proposal deduplication.

## Task projection

`GET /api/tasks/{id}` adds an optional `agent_wakeup` object for a delegated task. The object appears only on the task that triggered the current wake row. Other tasks delegated to the same agent keep the legacy runner guidance, so a viewer never reads the timing or outcome of a delegation on a task they cannot read.

The object contains only:

```json
{
  "status": "pending",
  "requested_at": "2026-08-24T12:00:00+00:00",
  "started_at": "",
  "finished_at": "",
  "reason": "",
  "automation_enabled": true
}
```

The projection does not return `requested_by`, `thread_id`, or raw exception text.

## Task Peek behavior

Task Peek shows these messages:

- `pending`: The agent turn is queued.
- `running`: The agent is working its delegated inbox.
- `completed`: The agent turn finished. Read the worklog or acceptance proposal.
- `refused`: The agent did not start. The message gives the safe reason.
- `failed`: The agent failed before the turn completed.
- `completion_unknown`: The turn can have written records. Read the worklog and Inbox before retrying.

If no wake row exists, Task Peek keeps the current runner and Chat guidance for legacy delegations.

## Startup and shutdown

Application startup recovers stale running rows on every boot. If background jobs are enabled, startup also starts the queue worker.

The worker is a daemon. Shutdown does not wait for a model turn. A process stop leaves the row as `running`, and the next startup changes it to `completion_unknown`.

## Testing strategy

Backend tests cover:

- Atomic enqueue with delegation
- Rollback behavior
- Pending coalescing
- Delegation during a running turn
- Worker claim concurrency
- Startup recovery
- Provider, budget, and authority refusals
- Completion and unknown completion
- Task-row locking during claim
- Safe task projection
- No automatic work for agent-origin delegation
- No remote or unrelated tools.

Frontend tests cover every wake state and the legacy fallback.

The browser test delegates a task and sees `pending` without waiting for a live model.

## Commands

```bash
cd backend && .venv/bin/pytest tests/test_agent_wakeups.py tests/test_agent_runner.py tests/test_delegation.py
cd frontend && npm test -- task-peek-activation.test.tsx
cd frontend && npm run build
./scripts/lint.sh
```

## Success criteria

The feature is complete when:

- A human delegation and its pending wake row commit together.
- A rollback leaves neither change.
- A live deployment starts the agent without a Chat message.
- The agent run stays inside all current safety limits.
- The task panel shows the durable wake state.
- A process restart loses no pending request.
- Skein never retries a turn whose completion is unknown.
- Mock mode writes no simulated progress.
