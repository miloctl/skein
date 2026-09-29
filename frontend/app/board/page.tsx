"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";

import { BoardCard, type BoardTask } from "@/components/board-card";
import { Card } from "@/components/card";
import { api, loadError } from "@/lib/api";

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

export default function BoardPage() {
  const [params, setParams] = useState<Params | null>(null);
  const [mine, setMine] = useState(false);
  const [board, setBoard] = useState<Board | null>(null);
  const [failure, setFailure] = useState("");
  const generation = useRef(0);

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
              <section key={column.status} aria-labelledby={id} className="min-w-0">
                <h2 id={id} className="mb-2 skein-section-title text-ink-3">
                  {column.label} ({cards.length})
                </h2>
                {cards.length ? (
                  <ul className="space-y-2">
                    {cards.map((task) => (
                      <li key={task.id}>
                        <BoardCard task={task} today={board.today} />
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
