"use client";

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";

import { actionError, api } from "@/lib/api";
import { isGated, subscribeGated } from "@/lib/gated";
import { reportStatus } from "@/lib/status";
import { VisibilityBadge } from "@/components/visibility-picker";
import { timeAgo } from "@/lib/time";

/** The landing place for every reference to a task.
 *
 *  Before this, My Day named the exact row ("task #12 due") and linked to
 *  /dashboard — the top of a thirteen-section page, where the reader hunted
 *  by eye. `/ask` citations and activity rows had the same dead end. The
 *  panel is addressed by `?task=<id>` so those links stay ordinary links.
 *
 *  Back-button-safe by construction: opening pushes a history entry and
 *  closing pops it, so Back closes the panel instead of leaving the page
 *  the reader was working on. A panel that swallowed Back would be worse
 *  than the scroll it replaces.
 */

export type PeekTask = {
  id: number;
  title: string;
  description?: string | null;
  status: string;
  priority: string;
  assignee?: string | null;
  due_date?: string | null;
  completed_at?: string | null;
  committed_week?: string | null;
  milestone_title?: string | null;
  engagement_name?: string | null;
  delegated_agent?: string | null;
  sponsor?: string | null;
  forge_url?: string | null;
  waiting_on_type?: string | null;
  waiting_on_id?: number | null;
  visibility?: string;
  crew_id?: number | null;
};

type WorklogRow = {
  id: number;
  author: string;
  note: string;
  created_at: string;
};

const PARAM = "task";

/** Read the id the URL is asking for. window.location, not useSearchParams:
 *  the latter puts every consuming page behind a Suspense boundary for a
 *  value that is never prerendered — the reasoning app/auth/callback records. */
function taskIdFromUrl(): number | null {
  if (typeof window === "undefined") return null;
  const raw = new URLSearchParams(window.location.search).get(PARAM);
  const id = Number(raw);
  return raw && Number.isInteger(id) && id > 0 ? id : null;
}

/** The one way to open the panel from a row. A plain <a href="?task=5"> would
 *  reload the whole page for a same-page query change, so this pushes the
 *  entry itself and announces it — Back still closes the panel, because the
 *  entry is real history and not private state. */
export function PeekLink({
  taskId,
  children,
  className = "",
  onActivate,
}: {
  taskId: number;
  children: React.ReactNode;
  className?: string;
  onActivate?: () => void;
}) {
  return (
    <button
      type="button"
      onClick={() => {
        onActivate?.();
        const url = new URL(window.location.href);
        url.searchParams.set(PARAM, String(taskId));
        window.history.pushState({}, "", url);
        window.dispatchEvent(new Event("skein-peek"));
      }}
      className={`text-left underline decoration-line-strong underline-offset-2 hover:decoration-ink-3 ${className}`}
    >
      {/* the verb is ADDED, never an aria-label: a label replaces the whole
          subtree, so the task title left the accessibility tree entirely and
          a screen reader read twelve rows as "Open task #4, Open task #31".
          Voice control was worse — the visible name was unspeakable. The
          sibling edit button already names the task this way. */}
      <span className="sr-only">Open </span>
      {children}
    </button>
  );
}

export function TaskPeek() {
  const [taskId, setTaskId] = useState<number | null>(null);
  const gated = useSyncExternalStore(subscribeGated, isGated, () => false);
  // Both results carry the id they belong to, and the render below ignores
  // any that does not match the open task. Storing them bare would need a
  // synchronous reset when the id changes — setState inside an effect body,
  // which cascades renders — and would flash the previous task's worklog
  // under the new task's title for one frame.
  const [loaded, setLoaded] = useState<{
    id: number;
    task?: PeekTask;
    error?: string;
  } | null>(null);
  const [log, setLog] = useState<{ id: number; rows: WorklogRow[] } | null>(
    null,
  );
  const closeRef = useRef<HTMLButtonElement>(null);
  // where focus was before the panel took it — returning it is what keeps a
  // keyboard reader from being dropped at the top of the document on close
  const restoreFocus = useRef<HTMLElement | null>(null);

  // popstate fires for Back/Forward; the custom event covers same-page opens,
  // which pushState does NOT announce to anyone
  useEffect(() => {
    const sync = () => setTaskId(taskIdFromUrl());
    sync();
    window.addEventListener("popstate", sync);
    window.addEventListener("skein-peek", sync);
    return () => {
      window.removeEventListener("popstate", sync);
      window.removeEventListener("skein-peek", sync);
    };
  }, []);

  const close = useCallback(() => {
    const url = new URL(window.location.href);
    if (url.searchParams.get(PARAM)) window.history.back();
    setTaskId(null);
  }, []);

  // Focus handling only — an external system, which is what an effect is for.
  // Returning focus to whatever opened the panel is the difference between a
  // keyboard reader continuing where they were and being dropped at the top
  // of the document.
  useEffect(() => {
    if (!taskId) {
      const el = restoreFocus.current;
      // .focus() on a DETACHED node silently no-ops, and the search
      // dropdown unmounts its own row when the peek opens — so the trigger
      // is gone by the time we restore, and focus lands on <body>: the exact
      // drop this ref exists to prevent. Fall back to the search box.
      if (el?.isConnected) el.focus();
      else document.getElementById("nav-search")?.focus();
      restoreFocus.current = null;
      return;
    }
    restoreFocus.current = document.activeElement as HTMLElement;
  }, [taskId]);

  // re-read after a write from inside the panel. A bumped nonce, not a
  // direct setState from the child: the fetch effect stays the single place
  // that owns `loaded`.
  const [nonce, setNonce] = useState(0);
  const reload = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    if (!taskId) return;
    let live = true;
    api<PeekTask>(`/api/tasks/${taskId}`)
      .then((t) => live && setLoaded({ id: taskId, task: t }))
      // 404 covers "no such task" AND "not yours to read", deliberately —
      // services/scope.py raises the same sentence for both, because any
      // other pairing answers "does #12 exist" for sequential ids
      .catch((e) => live && setLoaded({ id: taskId, error: actionError(e) }));
    api<WorklogRow[]>(`/api/tasks/${taskId}/worklog`)
      .then((w) => live && setLog({ id: taskId, rows: w }))
      .catch(() => live && setLog({ id: taskId, rows: [] }));
    return () => {
      live = false;
    };
  }, [taskId, nonce]);

  useEffect(() => {
    // `gated` too, and not only in the render below: an effect still runs for
    // a component that returns null, so a ?task= link on a gated page inerted
    // every body sibling — the gate included — and locked the page out with
    // no panel on screen to explain it.
    if (!taskId || gated) return;
    // aria-modal prunes the screen reader's buffer; it does NOT touch the
    // browser's Tab order. Without inert, three Tabs walk out of the panel
    // into content the reader has just been told does not exist — a focus
    // black hole, which is worse than claiming no modality at all.
    // Siblings, because the panel is a body child too.
    const others = [...document.body.children].filter(
      (el) => !el.contains(closeRef.current),
    ) as HTMLElement[];
    others.forEach((el) => el.setAttribute("inert", ""));
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      // This gives back inert on nodes another component may also want inert
      // (the nav, while the auth gate stands). That is safe only because
      // nav.tsx re-asserts its own in an effect, and React runs every cleanup
      // in a commit before any effect body — never make the nav's inert a
      // rendered prop again, which this silently strips for good.
      others.forEach((el) => el.removeAttribute("inert"));
      document.removeEventListener("keydown", onKey);
    };
  }, [taskId, close, gated]);

  // a digest link carries ?task=12, and this panel opens on it unconditionally
  // — over the gate, as an aria-modal dialog that prunes the gate from the
  // screen reader's buffer while the gate moves focus into it
  if (!taskId || gated) return null;

  // results from a previous id are ignored rather than cleared — see `loaded`
  const task = loaded?.id === taskId ? loaded.task : undefined;
  const error = loaded?.id === taskId ? loaded.error : undefined;
  const worklog = log?.id === taskId ? log.rows : null;

  return (
    <div
      className="fixed inset-0 z-40 flex justify-end"
      // the scrim is not the dialog: a click target with no name and no role,
      // so it stays aria-hidden and Escape above carries the keyboard path
      onMouseDown={(e) => e.target === e.currentTarget && close()}
    >
      <div aria-hidden className="absolute inset-0 bg-ink/20" />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={task ? `Task #${task.id}: ${task.title}` : `Task #${taskId}`}
        className="relative flex h-full w-full max-w-md flex-col gap-3 overflow-y-auto border-l border-line bg-card p-4 shadow-card"
      >
        <div className="flex items-start justify-between gap-2">
          {/* live: focus moves to Close in the same commit as the open, while
              the fetch is still in flight, so the panel announced a bare
              number and the title that arrived later was never spoken */}
          <h2 aria-live="polite" className="text-sm font-medium text-ink">
            <span className="text-ink-3">#{taskId}</span>{" "}
            {task?.title ?? (error ? "Not available" : "Loading…")}
          </h2>
          <button
            ref={closeRef}
            onClick={close}
            aria-label="Close the task panel"
            className="rounded-lg bg-raised px-2 py-0.5 text-xs text-ink-2 hover:bg-line"
          >
            Close
          </button>
        </div>

        {error ? (
          <p className="text-sm text-danger">{error}</p>
        ) : !task ? (
          <p className="text-sm text-ink-3">Loading…</p>
        ) : (
          <>
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
              <Row label="Status" value={task.status} />
              <Row label="Priority" value={task.priority} />
              <Row label="Assignee" value={task.assignee ? `@${task.assignee}` : ""} />
              <Row label="Due" value={task.due_date} />
              <Row label="Week" value={task.committed_week} />
              <Row label="Milestone" value={task.milestone_title} />
              <Row label="Engagement" value={task.engagement_name} />
              <Row
                label="Waiting on"
                value={
                  task.waiting_on_type && task.waiting_on_id
                    ? `${task.waiting_on_type} #${task.waiting_on_id}`
                    : ""
                }
              />
              <Row
                label="Delegated"
                value={
                  task.delegated_agent
                    ? `${task.delegated_agent} (sponsor ${task.sponsor || "none"})`
                    : ""
                }
              />
            </dl>
            <p className="text-xs">
              <VisibilityBadge
                visibility={String(task.visibility ?? "workspace")}
                crewId={task.crew_id as number}
              />
            </p>
            {task.description ? (
              <p className="whitespace-pre-wrap break-words text-sm text-ink-2">
                {task.description}
              </p>
            ) : null}
            {task.forge_url ? (
              // bare href is safe: services/forge.py::_clean_url is the only
              // writer and admits bounded http(s) only
              <a
                href={String(task.forge_url)}
                target="_blank"
                rel="noopener noreferrer"
                className="text-xs text-ink-3 underline hover:text-ink-2"
              >
                code <span aria-hidden>↗</span>
              </a>
            ) : null}

            {/* Delegation, from the UI. The Agents page empty state points
                at this control, so removing it leaves that copy advertising
                something nothing offers. Sponsor defaults to the caller
                server-side, which is the honest default: whoever hands the
                work out answers for it. */}
            {!task.delegated_agent && task.status !== "done" ? (
              <Delegate taskId={task.id} onDone={reload} />
            ) : null}

            {/* The worklog is readable BEFORE the sponsor's verdict by
                design (services/delegation.py::list_worklog) — this panel is
                where a sponsor watches delegated work progress, and the only
                place in the web app that shows it. */}
            <h3 className="mt-2 font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-ink-3">
              Worklog
            </h3>
            {worklog === null ? (
              <p className="text-xs text-ink-3">Loading…</p>
            ) : worklog.length === 0 ? (
              <p className="text-xs text-ink-3">
                {task.delegated_agent
                  ? "No progress notes yet."
                  : "Worklog notes come from delegated work."}
              </p>
            ) : (
              <ul className="space-y-2">
                {worklog.map((w) => (
                  <li key={w.id} className="text-xs">
                    <span className="text-ink-3">
                      {w.author} · {timeAgo(w.created_at)}
                    </span>
                    <p className="whitespace-pre-wrap break-words text-ink-2">
                      {w.note}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </aside>
    </div>
  );
}

/** Hand a task to an agent. The roster comes from mission control, which is
 *  the same list /agents shows — a free-text field here would let a typo mint
 *  a brand-new agent identity that nobody has granted any authority to. */
function Delegate({ taskId, onDone }: { taskId: number; onDone: () => void }) {
  const [agents, setAgents] = useState<string[] | null>(null);
  const [picked, setPicked] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<{ agent: string }[]>("/api/agents")
      .then((rows) => setAgents(rows.map((r) => r.agent)))
      .catch(() => setAgents([]));
  }, []);

  if (agents === null) return null;
  if (agents.length === 0)
    return (
      <p className="text-xs text-ink-3">
        No agent identities yet. An agent appears here after its first turn.
      </p>
    );

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <label className="sr-only" htmlFor={`delegate-${taskId}`}>
        Delegate to
      </label>
      <select
        id={`delegate-${taskId}`}
        value={picked}
        onChange={(e) => setPicked(e.target.value)}
        className="rounded-lg border border-line-strong bg-transparent px-2 py-1 text-xs outline-none focus:border-thread-solid"
      >
        <option value="">Delegate to…</option>
        {agents.map((a) => (
          <option key={a} value={a}>
            {a}
          </option>
        ))}
      </select>
      <button
        disabled={!picked || busy}
        onClick={async () => {
          setBusy(true);
          try {
            await api(`/api/tasks/${taskId}/delegate`, {
              method: "POST",
              body: JSON.stringify({ agent: picked }),
            });
            reportStatus(`Task #${taskId} delegated to ${picked}. You are the sponsor.`);
            onDone();
          } catch (e) {
            reportStatus(actionError(e));
          } finally {
            setBusy(false);
          }
        }}
        className="rounded-lg bg-thread-solid px-2 py-1 text-xs font-medium text-white hover:opacity-90 disabled:opacity-40"
      >
        Delegate
      </button>
    </div>
  );
}

function Row({ label, value }: { label: string; value?: string | null }) {
  if (!value) return null;
  return (
    <>
      <dt className="text-ink-3">{label}</dt>
      <dd className="text-ink-2">{value}</dd>
    </>
  );
}
