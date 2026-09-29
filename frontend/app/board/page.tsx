"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";

import { BoardCard, COLUMN_LABELS, type BoardTask, type Moves, type Panel } from "@/components/board-card";
import { Card } from "@/components/card";
import { actionError, api, loadError } from "@/lib/api";
import { reportStatus } from "@/lib/status";

/** GET /api/tasks/board. */
type Board = {
  scope: { kind: "engagement" | "milestone"; id: number; title: string } | null;
  limit: number;
  done_days: number;
  today: string;
  open: BoardTask[];
  done: BoardTask[];
};

type Params = { engagement: number; milestone: number };

// void is not a column: the task panel keeps its own confirmed void control
const COLUMNS = [
  { status: "todo", label: "To do", empty: "Nothing waiting." },
  { status: "in_progress", label: "In progress", empty: "Nothing in progress." },
  { status: "blocked", label: "Blocked", empty: "Nothing is blocked." },
  { status: "done", label: "Done", empty: "" },
] as const;

const positive = (raw: string | null) => {
  const id = Number(raw);
  return raw && Number.isInteger(id) && id > 0 ? id : 0;
};

/** Reports `?engagement=` and `?milestone=`. In its own Suspense boundary, as
 *  app/notes/page.tsx explains for `?note=`. Primitive values: the task panel
 *  pushes `?task=` on this page, and an unchanged pair must not reload. */
function BoardParams({ onChange }: { onChange: (p: Params) => void }) {
  const search = useSearchParams();
  const engagement = positive(search.get("engagement"));
  const milestone = positive(search.get("milestone"));
  useEffect(() => onChange({ engagement, milestone }), [engagement, milestone, onChange]);
  return null;
}

const POINTER = "(pointer: fine)";
const finePointer = () => !!window.matchMedia?.(POINTER).matches;
const subscribePointer = (onChange: () => void) => {
  const query = window.matchMedia?.(POINTER);
  query?.addEventListener("change", onChange);
  return () => query?.removeEventListener("change", onChange);
};

export default function BoardPage() {
  const [params, setParams] = useState<Params | null>(null);
  const [mine, setMine] = useState(false);
  const [board, setBoard] = useState<Board | null>(null);
  const [failure, setFailure] = useState("");
  const generation = useRef(0);
  const [panel, setPanel] = useState<Panel>(null);
  const [moving, setMoving] = useState<number | null>(null);
  const inFlight = useRef(false);
  const [dropTarget, setDropTarget] = useState("");
  const dragged = useRef<number | null>(null);
  // Drag only for a fine pointer: on touch a long-press drag fights the
  // scroll, and the Move button is the touch path
  const fine = useSyncExternalStore(subscribePointer, finePointer, () => false);
  // the card's Move button in its NEW column, once the reload has drawn it
  const pendingFocus = useRef<number | null>(null);

  const onParams = useCallback(
    (next: Params) =>
      setParams((cur) =>
        cur && cur.engagement === next.engagement && cur.milestone === next.milestone ? cur : next,
      ),
    [],
  );

  const load = useCallback(() => {
    if (!params) return;
    const query = new URLSearchParams();
    if (params.engagement) query.set("engagement_id", String(params.engagement));
    if (params.milestone) query.set("milestone_id", String(params.milestone));
    if (mine) query.set("mine", "true");
    // one answer wins: a focus reload that returns after a newer one must
    // not put the older board back
    const g = ++generation.current;
    // no-store: api() serves a GET from a 15-second cache (lib/api.ts), and a
    // reload inside that window shows the board from before another move
    api<Board>(`/api/tasks/board?${query}`, { cache: "no-store" })
      .then((data) => {
        if (g !== generation.current) return;
        setBoard(data);
        setFailure("");
      })
      .catch((e) => {
        if (g === generation.current) setFailure(loadError(e));
      });
  }, [params, mine]);

  useEffect(load, [load]);

  useEffect(() => {
    const id = pendingFocus.current;
    if (id === null) return;
    pendingFocus.current = null;
    document.getElementById(`board-move-${id}`)?.focus();
  }, [board]);

  const focusMove = (id: number) => document.getElementById(`board-move-${id}`)?.focus();

  // One write at a time. There is no optimistic move: the card stays where
  // it is until the server answers, then the board rereads, so a refused
  // move never leaves the screen wrong.
  const write = async (task: BoardTask, work: () => Promise<void>) => {
    if (inFlight.current) return;
    inFlight.current = true;
    setMoving(task.id);
    // focus leaves the panel before it closes, or it drops to the page
    focusMove(task.id);
    setPanel(() => null);
    try {
      await work();
    } catch (e) {
      // a 409 names the task's current status, and the reload shows it
      reportStatus(actionError(e));
    } finally {
      inFlight.current = false;
      setMoving(null);
      pendingFocus.current = task.id;
      load();
    }
  };

  /** The one move rule, for the drop and for the Move panel
   *  (docs/intent/board-view.md D5). */
  const move = (task: BoardTask, target: string) => {
    if (target === task.status || task.delegated_agent) return;
    // leaving Blocked is a board rule: the reader resolves what they can see
    if (task.blockers.length) return setPanel(() => ({ id: task.id, mode: "menu" }));
    if (target === "blocked") {
      // raise_blocker keeps a finished task finished (services/blockers.py)
      if (task.status === "done") {
        setPanel(() => null);
        return reportStatus(`Reopen task #${task.id} first. Then raise a blocker.`);
      }
      // the service sets Blocked when the blocker is filed, so no PATCH
      return setPanel(() => ({ id: task.id, mode: "blocker" }));
    }
    void write(task, async () => {
      await api(`/api/tasks/${task.id}`, {
        method: "PATCH",
        body: JSON.stringify({ status: target, expected_status: task.status }),
      });
      reportStatus(`Task #${task.id} moved to ${COLUMN_LABELS[target]}.`, "confirmation");
    });
  };

  const moves: Moves = {
    panel,
    setPanel,
    moving,
    move,
    resolve: (task, blockerId) =>
      void write(task, async () => {
        const out = await api<{ task_unblocked: boolean }>(`/api/blockers/${blockerId}/resolve`, {
          method: "POST",
          body: JSON.stringify({ resolution: "resolved from the board" }),
        });
        reportStatus(
          out.task_unblocked
            ? `Blocker #${blockerId} is resolved. Task #${task.id} moved to In progress.`
            : `Blocker #${blockerId} is resolved.`,
          "confirmation",
        );
      }),
    settle: (task, raised) => {
      setPanel(() => null);
      if (raised) {
        pendingFocus.current = task.id;
        load();
      } else focusMove(task.id);
    },
  };

  const cardOnBoard = (id: number) =>
    board ? [...board.open, ...board.done].find((t) => t.id === id) : undefined;

  // The board reloads on events, never on a timer (D10): a capture or a
  // verdict, an edit in the task panel, and a return to the tab.
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") load();
    };
    window.addEventListener("skein-attention-change", load);
    window.addEventListener("skein-peek-close", load);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener("skein-attention-change", load);
      window.removeEventListener("skein-peek-close", load);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [load]);

  const capped = !!board && board.open.length >= board.limit;
  const rows = (status: string) =>
    board ? (status === "done" ? board.done : board.open.filter((t) => t.status === status)) : [];

  return (
    <main id="content" tabIndex={-1} className="mx-auto w-full max-w-6xl p-4 sm:p-6">
      <Suspense fallback={null}>
        <BoardParams onChange={onParams} />
      </Suspense>
      <div className="mb-4">
        <h1 className="font-display text-[24px]/[1.15] font-semibold tracking-[-0.01em] text-ink">
          Board
        </h1>
        <p className="mt-0.5 text-sm text-ink-3">
          Open work that you can read, by status, and the work finished recently. Select a
          task to open it.
        </p>
      </div>

      <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
        {board?.scope ? (
          <p className="text-ink-2">
            {board.scope.kind === "engagement" ? (
              <>
                Engagement:{" "}
                <Link href={`/engagement/${board.scope.id}`} className="underline underline-offset-2">
                  {board.scope.title}
                </Link>
              </>
            ) : (
              <>Milestone: {board.scope.title}</>
            )}
            {" · "}
            <Link href="/board" className="underline underline-offset-2">
              Show all work
            </Link>
          </p>
        ) : null}
        <label className="flex min-h-6 items-center gap-1 text-ink-2">
          <input type="checkbox" checked={mine} onChange={(e) => setMine(e.target.checked)} />
          Only my tasks
        </label>
      </div>

      {failure ? <p className="mb-3 text-sm text-danger">{failure}</p> : null}
      {capped ? (
        <p className="mb-3 text-sm text-ink-2">
          This board shows the first {board.limit} open tasks, highest priority first. Open one
          engagement to see the rest.
        </p>
      ) : null}

      {!board ? (
        failure ? null : (
          <Card>
            <p className="text-sm text-ink-3">Loading…</p>
          </Card>
        )
      ) : (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-4">
          {COLUMNS.map((column) => {
            const cards = rows(column.status);
            const id = `board-column-${column.status}`;
            // at the cap, rows past it can belong to an empty open column,
            // so the column claims nothing
            const empty =
              column.status === "done"
                ? `Nothing finished in the last ${board.done_days} days.`
                : capped
                  ? ""
                  : column.empty;
            return (
              <section
                key={column.status}
                aria-labelledby={id}
                onDragOver={(e) => {
                  // a card on this board, not already in this column
                  const task = dragged.current === null ? undefined : cardOnBoard(dragged.current);
                  if (!task || task.status === column.status) return;
                  e.preventDefault();
                  setDropTarget(column.status);
                }}
                onDragLeave={(e) => {
                  if (!e.currentTarget.contains(e.relatedTarget as Node)) setDropTarget("");
                }}
                onDrop={(e) => {
                  e.preventDefault();
                  setDropTarget("");
                  dragged.current = null;
                  // another page's drag lands here too: only a card on this
                  // board moves
                  const task = cardOnBoard(Number(e.dataTransfer.getData("text/plain")));
                  if (task) move(task, column.status);
                }}
                // an outline, not a ring: Tailwind's ring is a box-shadow,
                // and forced-colors mode drops box-shadows (app/globals.css)
                className={`min-w-0 rounded-lg ${dropTarget === column.status ? "outline-2 outline-dashed outline-thread" : ""}`}
              >
                <h2 id={id} className="mb-2 skein-section-title text-ink-3">
                  {column.label} ({cards.length})
                </h2>
                {cards.length ? (
                  <ul className="space-y-2">
                    {cards.map((task) => (
                      <li
                        key={task.id}
                        draggable={fine && !task.delegated_agent}
                        onDragStart={(e) => {
                          e.dataTransfer.setData("text/plain", String(task.id));
                          dragged.current = task.id;
                        }}
                        onDragEnd={() => {
                          dragged.current = null;
                          setDropTarget("");
                        }}
                      >
                        <BoardCard task={task} today={board.today} moves={moves} />
                      </li>
                    ))}
                  </ul>
                ) : empty ? (
                  <p className="text-xs text-ink-3">{empty}</p>
                ) : null}
              </section>
            );
          })}
        </div>
      )}
    </main>
  );
}
