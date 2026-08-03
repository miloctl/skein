"use client";

import { useCallback, useEffect, useState } from "react";

import { api, loadError } from "@/lib/api";
import { Card } from "@/components/card";
import { SectionTabs } from "@/components/section-tabs";
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
    <main id="content" tabIndex={-1} className="mx-auto w-full max-w-5xl p-4 sm:p-6">
      <SectionTabs set="team" />
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold text-ink">Activity</h1>
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
      {loaded && !error && entries.length === 0 && (
        <Card>
          <p className="text-sm text-ink-3">
            Nothing on the ledger yet. Rows appear here as agents and the
            system do work.
          </p>
        </Card>
      )}

      {entries.length > 0 && (
        <Card>
          <ul className="divide-y divide-line">
            {entries.map((e) =>
              raw ? (
                <li key={e.seq} className="py-1.5 font-mono text-[11px] text-ink-2">
                  <span className="text-ink-3">#{e.seq}</span> {e.created_at}{" "}
                  <span className="font-medium text-thread">{e.actor}</span> {e.action}{" "}
                  {e.detail}
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
                    <span className="min-w-0 flex-1 truncate text-ink">
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
                      <div>seq #{e.seq} · {e.created_at}</div>
                      <div>
                        actor {e.actor} · action {e.action}
                        {!e.registered && " · (no verb registered — shown as recorded)"}
                      </div>
                      {e.detail && <div className="break-all">detail: {e.detail}</div>}
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
