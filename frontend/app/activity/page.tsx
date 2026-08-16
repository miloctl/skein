"use client";

import { useCallback, useEffect, useState } from "react";

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

/** The task id an activity row names, or null.
 *
 *  Keyed on the ACTION, never on the shape of `detail`. Real rows read
 *  "escalated a blocker — #5 …" and "minted an API key — #29 bootstrap":
 *  the entity word lives in the SENTENCE the verb registry produces, and the
 *  detail is a bare `#N` for every entity alike. A parser that read the
 *  detail therefore opened the task panel on a blocker id or an API-key id —
 *  the wrong row, or a row that does not exist, and both look like the
 *  feature working.
 *
 *  The action is authoritative because services/activity.py's registry is
 *  what names the entity in the first place. An action missing from this set
 *  simply gets no link, which is the safe direction: a row with no link is a
 *  row the reader expands, exactly as before. */
const TASK_ACTIONS = new Set([
  "create_task",
  "update_task",
  "complete_task",
  "delegate_task",
  "claim_task",
  "report_progress",
]);

export function taskRef(action: string, detail: string): number | null {
  if (!TASK_ACTIONS.has(String(action ?? ""))) return null;
  const m = /(?:^|\s)#(\d+)\b/.exec(String(detail ?? ""));
  if (!m) return null;
  const id = Number(m[1]);
  return Number.isInteger(id) && id > 0 ? id : null;
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
            {entries.map((e) =>
              raw ? (
                <li
                  key={e.seq}
                  className="py-1.5 font-mono text-[11px] text-ink-2"
                >
                  <span className="text-ink-3">#{e.seq}</span> {e.created_at}{" "}
                  <span className="font-medium text-thread">{e.actor}</span>{" "}
                  {e.action} {e.detail}
                </li>
              ) : (
                <li key={e.seq}>
                  <button
                    onClick={() =>
                      setExpanded((cur) => (cur === e.seq ? null : e.seq))
                    }
                    aria-expanded={expanded === e.seq}
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
                        <span className="text-ink-3"> — {e.detail}</span>
                      )}
                    </span>
                    <span className="shrink-0 text-xs text-ink-3">
                      {timeAgo(e.created_at)}
                    </span>
                  </button>
                  {expanded === e.seq && (
                    <div className="mb-2 ml-6 rounded-lg bg-raised px-3 py-2 font-mono text-[11px] text-ink-2">
                      <div>
                        seq #{e.seq} · {e.created_at}
                      </div>
                      <div>
                        actor {e.actor} · action {e.action}
                        {!e.registered &&
                          " · (no verb registered — shown as recorded)"}
                      </div>
                      {e.detail && (
                        <div className="break-all">detail: {e.detail}</div>
                      )}
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
              ),
            )}
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
