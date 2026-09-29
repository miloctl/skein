"use client";

import { MenuPanel } from "@/components/menu-panel";
import { RaiseBlockerForm } from "@/components/raise-blocker-form";
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

export const COLUMN_LABELS: Record<string, string> = {
  todo: "To do",
  in_progress: "In progress",
  blocked: "Blocked",
  done: "Done",
};

/** Which of the card's controls is open. One at a time, for the whole board
 *  (app/board/page.tsx owns it), so a drop can open the card's own panel. */
export type Panel = { id: number; mode: "menu" | "blocker" } | null;

export type Moves = {
  panel: Panel;
  setPanel: (update: (current: Panel) => Panel) => void;
  moving: number | null;
  move: (task: BoardTask, target: string) => void;
  resolve: (task: BoardTask, blockerId: number) => void;
  /** after a raise or a cancel: the page rereads and returns focus */
  settle: (task: BoardTask, raised: boolean) => void;
};

/** A card carries facts about the task, never about the person: no
 *  per-person totals, because a Done column beside names is a leaderboard
 *  (docs/intent/board-view.md D9). */
export function BoardCard({ task, today, moves }: { task: BoardTask; today: string; moves: Moves }) {
  const overdue = task.status !== "done" && !!task.due_date && task.due_date < today;
  const extra = task.blockers.length - SHOWN_BLOCKERS;
  const open = moves.panel?.id === task.id ? moves.panel.mode : null;
  const busy = moves.moving === task.id;
  const button = "block w-full rounded-lg px-2 py-1 text-left text-xs text-ink-2 hover:bg-raised";
  return (
    <div aria-busy={busy || undefined} className="rounded-lg border border-line bg-card p-2 text-xs">
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
      {/* A delegated task has no Move: only the sponsor's verdict in Inbox
          closes it, and the task panel says so */}
      {task.delegated_agent ? null : (
        <div className="relative mt-1">
          <button
            type="button"
            id={`board-move-${task.id}`}
            aria-expanded={open === "menu"}
            aria-disabled={busy || undefined}
            onClick={() => {
              if (!busy) moves.setPanel((cur) => (cur?.id === task.id ? null : { id: task.id, mode: "menu" }));
            }}
            className="rounded px-1.5 py-0.5 text-ink-3 hover:text-ink aria-disabled:opacity-50"
          >
            {busy ? "Moving…" : "Move"}{" "}
            <span className="sr-only">
              task #{task.id}: {task.title}
            </span>
          </button>
          {open === "menu" ? (
            <MenuPanel
              label={`Move task #${task.id}`}
              // only the menu closes itself: the swap to the blocker form
              // unmounts this panel, and a blur on the way out must not
              // close the form it opened
              onClose={() => moves.setPanel((cur) => (cur?.id === task.id && cur.mode === "menu" ? null : cur))}
            >
              {task.blockers.length ? (
                <>
                  <p className="px-2 py-1 text-ink-2">
                    Resolve its blockers to move it. When the last one is resolved, the task moves to
                    In progress.
                  </p>
                  {task.blockers.map((b) => (
                    <button key={b.id} type="button" onClick={() => moves.resolve(task, b.id)} className={button}>
                      Resolve #{b.id} {b.title}
                    </button>
                  ))}
                </>
              ) : (
                Object.entries(COLUMN_LABELS)
                  .filter(([status]) => status !== task.status)
                  .map(([status, label]) => (
                    <button key={status} type="button" onClick={() => moves.move(task, status)} className={button}>
                      {status === "blocked" ? `${label}…` : label}
                    </button>
                  ))
              )}
            </MenuPanel>
          ) : null}
        </div>
      )}
      {open === "blocker" ? (
        <div className="mt-2">
          <RaiseBlockerForm
            task={task}
            onRaised={() => moves.settle(task, true)}
            onCancel={() => moves.settle(task, false)}
          />
        </div>
      ) : null}
    </div>
  );
}
