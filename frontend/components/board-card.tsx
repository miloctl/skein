"use client";

import { PeekLink } from "@/components/task-peek";

/** One row of GET /api/tasks/board (routes/api.py::_TASK_BOARD_FIELDS). */
export type BoardTask = {
  id: number;
  title: string;
  status: string;
  priority: string;
  assignee: string;
  due_date: string | null;
  completed_at: string | null;
  forge_url: string | null;
  visibility: string;
  crew_id: number | null;
  committed_week: string | null;
  delegated_agent: string | null;
  waiting_on_type: string | null;
  waiting_on_id: number | null;
  quiet_days: number | null;
  blockers: { id: number; title: string }[];
};

const SHOWN_BLOCKERS = 2;

/** A card carries facts about the task, never about the person: no
 *  per-person totals, because a Done column beside names is a leaderboard
 *  (docs/intent/board-view.md D9). */
export function BoardCard({ task, today }: { task: BoardTask; today: string }) {
  const overdue = task.status !== "done" && !!task.due_date && task.due_date < today;
  const extra = task.blockers.length - SHOWN_BLOCKERS;
  return (
    <div className="rounded-lg border border-line bg-card p-2 text-xs">
      <PeekLink taskId={task.id} className="line-clamp-2 text-sm text-ink">
        <span className="text-ink-3">#{task.id}</span> {task.title}
      </PeekLink>
      <p className="mt-1 text-ink-3">
        {task.assignee ? `@${task.assignee}` : "unassigned"} · {task.priority}
        {task.due_date ? (
          <>
            {" · "}
            <span className={overdue ? "text-weld" : ""}>
              due {task.due_date}
              {overdue ? " · overdue" : ""}
            </span>
          </>
        ) : null}
        {task.committed_week ? (
          <span className="ml-1 rounded bg-raised px-1">{task.committed_week}</span>
        ) : null}
      </p>
      {task.blockers.length || task.waiting_on_type || task.delegated_agent || task.quiet_days ? (
        <ul className="mt-1 space-y-0.5">
          {task.blockers.slice(0, SHOWN_BLOCKERS).map((b) => (
            <li key={b.id} className="text-weld">
              Blocked by #{b.id} {b.title}
            </li>
          ))}
          {extra > 0 ? <li className="text-weld">+{extra} more</li> : null}
          {task.waiting_on_type ? (
            <li className="text-ink-3">
              Waiting on {task.waiting_on_type} #{task.waiting_on_id}
            </li>
          ) : null}
          {task.delegated_agent ? (
            <li className="text-ink-3">Delegated to {task.delegated_agent}</li>
          ) : null}
          {/* the stale-work marker Health uses (work.board_cards): a fact
              about the task, so no danger color */}
          {task.quiet_days ? (
            <li className="text-ink-3">Not moved for {task.quiet_days} days</li>
          ) : null}
        </ul>
      ) : null}
    </div>
  );
}
