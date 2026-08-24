"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { api, loadError } from "@/lib/api";
import { Card } from "@/components/card";
import { SectionTabs } from "@/components/section-tabs";
import { PeekLink } from "@/components/task-peek";
import { timeAgo } from "@/lib/time";

type Entry = {
  seq: number;
  actor: string;
  who: "you" | "agent" | "system";
  sentence: string;
  salience: "loud" | "normal" | "quiet";
  registered: boolean;
  action: string;
  detail: string;
  created_at: string;
};

type Feed = { entries: Entry[]; next_before: number | null };

const WHO_BADGE: Record<Entry["who"], string> = {
  you: "bg-thread-solid/15 text-thread",
  agent: "bg-raised text-weld",
  system: "bg-raised text-ink-2",
};

// moved to lib/task-ref.ts so My Day's digest can share the parser without
// importing this page's whole tree; re-exported because the tests and this
// page's rows both name it here
export { taskRef } from "@/lib/task-ref";
import { taskRef } from "@/lib/task-ref";
import { size } from "@/lib/size";

// The ledger stores the exact detail inside the hash chain. The sentence view
// humanizes it at render; Raw rows keeps the stored text.
function humanize(detail: string): string {
  return detail
    .replace(/\((\d+) bytes\)/, (_, n) => `(${size(Number(n))})`)
    .replace(
      /^#(\d+) create ([a-z_]+)(.*)$/i,
      (_, id, entity, rest) =>
        `proposal #${id} · add a ${entity.replaceAll("_", " ")}${rest}`,
    )
    .replace(
      /^#(\d+) update ([a-z_]+)(.*)$/i,
      (_, id, entity, rest) =>
        `proposal #${id} · update ${entity.replaceAll("_", " ")}${rest}`,
    );
}

export default function ActivityPage() {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [nextBefore, setNextBefore] = useState<number | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [raw, setRaw] = useState(false);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);

  const load = useCallback(() => {
    api<Feed>("/api/activity/feed")
      .then((f) => {
        setEntries(f.entries);
        setNextBefore(f.next_before);
        setError(null);
      })
      .catch((e) => setError(loadError(e)))
      .finally(() => setLoaded(true));
  }, []);
  useEffect(load, [load]);

  const more = async () => {
    if (nextBefore == null) return;
    setLoadingMore(true);
    try {
      const f = await api<Feed>(`/api/activity/feed?before=${nextBefore}`);
      setEntries((cur) => [...cur, ...f.entries]);
      setNextBefore(f.next_before);
    } catch (e) {
      setError(loadError(e));
    } finally {
      setLoadingMore(false);
    }
  };

  // Folded at render over the ACCUMULATED pages, not per fetch — a burst
  // split across a page boundary must not render as two groups. Keyed on the
  // raw actor, never `who`: `who` collapses every agent to the literal
  // "agent" and would merge two different agents' bursts into one row. The
  // feed itself shows only the viewer, agents, and system actors
  // (activity.visible_actor_filter), so two humans can never fold together.
  const runs = useMemo(() => {
    const out: Entry[][] = [];
    for (const e of entries) {
      const last = out[out.length - 1];
      if (last && last[0].actor === e.actor && last[0].action === e.action)
        last.push(e);
      else out.push([e]);
    }
    return out;
  }, [entries]);

  return (
    <main
      id="content"
      tabIndex={-1}
      className="mx-auto w-full max-w-5xl xl:max-w-6xl p-4 sm:p-6"
    >
      <SectionTabs set="team" />
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="font-display text-[24px]/[1.15] font-semibold tracking-[-0.01em] text-ink">
            Activity
          </h1>
          <p className="mt-0.5 text-sm text-ink-3">
            What the agents did, what the system did, and what you did — one
            sentence per action. Teammates&apos; rows are not shown here.
          </p>
        </div>
        <label className="flex cursor-pointer items-center gap-2 text-xs text-ink-2">
          <input
            type="checkbox"
            checked={raw}
            onChange={(e) => setRaw(e.target.checked)}
          />
          Raw rows
        </label>
      </div>

      {error && <p className="mb-3 text-sm text-danger">{error}</p>}
      {/* Before `loaded` this rendered nothing — no rows, no empty state, no
          spinner. The ledger feed is the largest read in the app, so the
          blank is longest exactly where it is least explicable. */}
      {!loaded && !error && (
        <Card>
          <p className="text-sm text-ink-3">Loading…</p>
        </Card>
      )}
      {loaded && !error && entries.length === 0 && (
        <Card>
          <p className="text-sm text-ink-3">
            Nothing on the ledger yet. Rows appear here as agents and the system
            do work.
          </p>
        </Card>
      )}

      {entries.length > 0 && (
        <Card>
          <ul className="divide-y divide-line">
            {(raw ? entries.map((e) => [e]) : runs).map((run) => {
              const e = run[0];
              return raw ? (
                <li
                  key={e.seq}
                  className="py-1.5 font-mono text-[11px] text-ink-2"
                >
                  <span className="text-ink-3">#{e.seq}</span> {e.created_at}{" "}
                  <span className="font-medium text-thread">{e.actor}</span>{" "}
                  {e.action} {e.detail}
                </li>
              ) : run.length > 1 ? (
                <li key={e.seq}>
                  <button
                    onClick={() =>
                      setExpanded((cur) => (cur === e.seq ? null : e.seq))
                    }
                    aria-expanded={expanded === e.seq}
                    aria-controls={`activity-${e.seq}-details`}
                    className={
                      "flex w-full items-baseline gap-2 py-2 text-left text-sm hover:bg-raised/50 " +
                      (run.every((c) => c.salience === "quiet")
                        ? "text-ink-2"
                        : "")
                    }
                  >
                    <span
                      aria-hidden
                      className={
                        "mt-1 inline-block size-2 shrink-0 self-center rounded-full " +
                        (run.some((c) => c.salience === "loud")
                          ? "bg-danger"
                          : "bg-line-strong")
                      }
                    />
                    <span
                      className={
                        "shrink-0 rounded-full px-1.5 py-px font-mono text-[10px] " +
                        WHO_BADGE[e.who]
                      }
                    >
                      {e.who === "you" ? "you" : e.who}
                    </span>
                    <span className="min-w-0 flex-1 break-words text-ink sm:truncate">
                      {e.sentence}
                      <span className="text-ink-3">
                        {" "}— {run.length} related actions
                      </span>
                    </span>
                    <span className="shrink-0 text-xs text-ink-3">
                      {timeAgo(e.created_at)}
                    </span>
                    <span className="shrink-0 text-xs font-medium text-thread">
                      Details <span aria-hidden>{expanded === e.seq ? "▾" : "▸"}</span>
                    </span>
                  </button>
                  {expanded === e.seq && (
                    <div
                      id={`activity-${e.seq}-details`}
                      className="mb-2 ml-6 space-y-1 rounded-lg bg-raised px-3 py-2 text-xs text-ink-2"
                    >
                      {run.map((entry) => (
                        <p key={entry.seq} className="break-words">
                          {entry.sentence}
                          {entry.detail ? ` — ${humanize(entry.detail)}` : ""}
                        </p>
                      ))}
                    </div>
                  )}
                </li>
              ) : (
                <li key={e.seq}>
                  <button
                    onClick={() =>
                      setExpanded((cur) => (cur === e.seq ? null : e.seq))
                    }
                    aria-expanded={expanded === e.seq}
                    aria-controls={`activity-${e.seq}-details`}
                    className={
                      "flex w-full items-baseline gap-2 py-2 text-left text-sm hover:bg-raised/50 " +
                      (e.salience === "quiet" ? "text-ink-2" : "")
                    }
                  >
                    <span
                      aria-hidden
                      className={
                        "mt-1 inline-block size-2 shrink-0 self-center rounded-full " +
                        (e.salience === "loud" ? "bg-danger" : "bg-line-strong")
                      }
                    />
                    <span
                      className={
                        "shrink-0 rounded-full px-1.5 py-px font-mono text-[10px] " +
                        WHO_BADGE[e.who]
                      }
                    >
                      {e.who === "you" ? "you" : e.who}
                    </span>
                    {/* wraps at phone width, truncates from sm up. At 360 the
                        row had 174px for a 576px sentence, so two thirds of
                        every line was gone and expanding reveals only `detail`
                        — a sighted phone reader had no route to the sentence
                        except the Raw toggle. */}
                    <span className="min-w-0 flex-1 break-words text-ink sm:truncate">
                      {e.sentence}
                      {e.detail && (
                        <span className="text-ink-3"> — {humanize(e.detail)}</span>
                      )}
                    </span>
                    <span className="shrink-0 text-xs text-ink-3">
                      {timeAgo(e.created_at)}
                    </span>
                    <span className="shrink-0 text-xs font-medium text-thread">
                      Details <span aria-hidden>{expanded === e.seq ? "▾" : "▸"}</span>
                    </span>
                  </button>
                  {expanded === e.seq && (
                    <div
                      id={`activity-${e.seq}-details`}
                      className="mb-2 ml-6 rounded-lg bg-raised px-3 py-2 text-xs text-ink-2"
                    >
                      <p className="break-words">
                        {e.sentence}
                        {e.detail ? ` — ${humanize(e.detail)}` : ""}
                      </p>
                      {/* In the EXPANDED panel, not the row: the row is a
                          <button>, and a link inside a button is invalid and
                          unreachable for a keyboard reader. It is also where
                          a reader who wants this row's detail already is. */}
                      {taskRef(e.action, e.detail) !== null && (
                        <div className="mt-1">
                          <PeekLink taskId={taskRef(e.action, e.detail) as number}>
                            {/* "task #31", not "open task #31": PeekLink
                                already prefixes an sr-only "Open ", so the
                                accessible name was "Open open task #31" */}
                            task #{taskRef(e.action, e.detail)}
                          </PeekLink>
                        </div>
                      )}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
          {nextBefore != null && (
            <button
              onClick={more}
              disabled={loadingMore}
              className="mt-3 w-full rounded-lg border border-line py-1.5 text-xs text-ink-2 hover:bg-raised disabled:opacity-50"
            >
              {loadingMore ? "Loading…" : "Older entries"}
            </button>
          )}
        </Card>
      )}
    </main>
  );
}
