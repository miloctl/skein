"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { actionError, api, loadError } from "@/lib/api";
import { reportStatus } from "@/lib/status";
import { EmptyState } from "@/components/card";
import { SectionTabs } from "@/components/section-tabs";

type Decision = {
  id: number;
  title: string;
  decision: string;
  context: string;
  decided_by: string;
  review_by: string | null;
  status: string;
  category: string;
  created_at: string;
};

/** The id a `#charter-entry-N` deep link is asking for, or 0.
 *
 *  Search hits and the My Day stale-decision item both address a decision this
 *  way, and both can name a decision in ANY category. This page defaults to
 *  the charter category, so those links landed on a list that never contained
 *  their target and the reader saw a page with no error and no row.
 */
function hashTarget(): number {
  if (typeof window === "undefined") return 0;
  const m = /^#charter-entry-(\d+)$/.exec(window.location.hash);
  return m ? Number(m[1]) : 0;
}

export default function CharterPage() {
  // null until the fetch settles: an empty charter is a real and meaningful
  // answer ("nobody has written the working agreements yet"), so it must not
  // also be what a slow or failed load looks like
  const [decisions, setDecisions] = useState<Decision[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  // computed in an effect, not a lazy initializer: a prerendered page bakes
  // the BUILD day's date into the served HTML, which is both wrong for the
  // reader and a hydration mismatch against the client's recomputed value
  const [reviewBy, setReviewBy] = useState("");
  useEffect(() => {
    // one-shot client init, not a cascading render: a lazy initializer would
    // bake the BUILD day's date into the prerendered HTML — wrong for the
    // reader, and a hydration mismatch against the client's recomputed value
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setReviewBy(
      new Date(Date.now() + 90 * 86400_000).toISOString().slice(0, 10),
    );
  }, []);
  const [superseding, setSuperseding] = useState<number | null>(null);
  // dismissing the editor hands focus back to its trigger, or a keyboard user
  // lands at the top of the page (same idiom: closeAsk in app/review/page.tsx)
  const closeSupersede = (id: number) => {
    setSuperseding(null);
    setTimeout(() => document.getElementById(`supersede-${id}`)?.focus(), 0);
  };
  const [busy, setBusy] = useState(false); // a held Enter must not file N entries
  const [newText, setNewText] = useState("");

  // Which slice the list shows. Charter first, because that is what this page
  // is for; "all" exists because the general decisions carry the SAME
  // lifecycle (review_by, stale, reconfirm, supersede) and had no surface
  // offering it — Browse renders them read-only.
  const [showAll, setShowAll] = useState(false);
  // Which row the slice was last widened FOR, not a bare "has widened" flag.
  // The guard exists because a link to a general decision widens, re-loads,
  // still does not match on the first render pass, and would set state again
  // on every settle. Keyed on the id, that loop is still closed — and a reader
  // who narrows back to Charter can follow a second link, which a boolean
  // latched shut for the rest of the visit.
  const widenedFor = useRef(0);

  const load = useCallback(() => {
    const q = showAll ? "" : "?category=charter";
    api<Decision[]>(`/api/decisions${q}`)
      .then((rows) => {
        setDecisions(rows);
        setError(null); // a recovered backend must not leave the old banner above fresh data
        // A deep link naming a decision this slice does not hold widens the
        // slice instead of showing the reader an empty page. The link is the
        // evidence that they were sent here for a specific row.
        const want = hashTarget();
        if (want && widenedFor.current !== want && !rows.some((r) => r.id === want)) {
          widenedFor.current = want;
          setShowAll(true);
        }
      })
      .catch((e) => {
        setDecisions([]); // settled, with the error shown below
        setError(loadError(e));
      });
  }, [showAll]);
  useEffect(load, [load]);

  // Land on the row the link named. The browser only honours a hash for an
  // element present at navigation time, and these rows arrive from a fetch —
  // so a deep link scrolled nowhere until the element existed.
  useEffect(() => {
    const want = hashTarget();
    if (!want || !decisions?.some((d) => d.id === want)) return;
    document.getElementById(`charter-entry-${want}`)?.focus();
  }, [decisions]);

  // ...and again when only the FRAGMENT changes. Arriving from another page
  // remounts this component, but a search hit picked while already on
  // /charter changes nothing else: no remount, no re-fetch, so neither the
  // focus effect above nor the auto-widen in `load` runs and the click is a
  // silent no-op.
  //
  // Both events, because neither covers the other: `hashchange` is the
  // browser's own (a typed address, Back between two fragments), and
  // `skein-hash` is what an in-app link announces — a next/link soft
  // navigation fires neither of the browser's (components/nav-search.tsx).
  useEffect(() => {
    const onHash = (ev: Event) => {
      // the id from the event when an in-app link sent one, the address bar
      // otherwise. `hashchange` carries no detail, and by the time an in-app
      // link's transition lands the address bar is right too — but reading it
      // AT dispatch is a frame too early, which is the whole reason the id
      // travels with the event (components/nav-search.tsx).
      const sent = (ev as CustomEvent<{ id?: number }>).detail?.id;
      const want = sent ?? hashTarget();
      if (!want) return;
      if (decisions?.some((d) => d.id === want)) {
        document.getElementById(`charter-entry-${want}`)?.focus();
      } else if (widenedFor.current !== want) {
        widenedFor.current = want;
        setShowAll(true);
      }
    };
    window.addEventListener("hashchange", onHash);
    window.addEventListener("skein-hash", onHash);
    return () => {
      window.removeEventListener("hashchange", onHash);
      window.removeEventListener("skein-hash", onHash);
    };
  }, [decisions]);

  const add = async () => {
    if (busy || !title.trim() || !text.trim()) return;
    setBusy(true);
    try {
      await api("/api/decisions", {
        method: "POST",
        body: JSON.stringify({
          title,
          decision: text,
          review_by: reviewBy,
          category: "charter",
        }),
      });
      setTitle("");
      setText("");
      load();
    } catch (e) {
      reportStatus(actionError(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <main
      id="content"
      tabIndex={-1}
      className="mx-auto w-full max-w-5xl xl:max-w-6xl p-4 sm:p-6"
    >
      <SectionTabs set="team" />
      <h1 className="mb-1 font-display text-[24px]/[1.15] font-semibold tracking-[-0.01em] text-ink">
        Team charter & decision rights
      </h1>
      <p className="mb-6 max-w-3xl text-sm text-ink-3">
        Mission, ownership, escalation rules, and working agreements live here
        as decisions with review dates. Each one gets reconfirmed or superseded
        instead of silently rotting.
      </p>
      {error && <p className="text-sm text-danger">{error}</p>}

      {/* The general decisions carry the same half-life, stale sweep,
          reconfirm and supersede that charter entries do, and no surface
          offered those controls — Browse lists them read-only. A slice
          switch is the whole fix: same rows, same lifecycle, one page. */}
      <div
        role="group"
        aria-label="Which decisions to show"
        className="mb-4 flex gap-1 text-xs"
      >
        {[
          { on: false, label: "Charter" },
          { on: true, label: "All decisions" },
        ].map((o) => (
          <button
            key={o.label}
            aria-pressed={showAll === o.on}
            onClick={() => {
              // the reader has taken the slice back, so the auto-widen guard
              // has nothing left to protect. Without this reset it stays
              // latched on the row it widened for, and following that same
              // link again lands on a list that does not contain it.
              widenedFor.current = 0;
              setShowAll(o.on);
            }}
            className={
              "rounded-lg px-2.5 py-1 font-medium " +
              (showAll === o.on
                ? "bg-thread-solid text-white"
                : "bg-raised text-ink-2 hover:bg-line")
            }
          >
            {o.label}
          </button>
        ))}
      </div>

      <div className="mb-6 space-y-2 rounded-xl border border-line bg-card p-4 shadow-card">
        <input
          value={title}
          maxLength={200}
          aria-label="Charter entry title"
          onChange={(e) => setTitle(e.target.value)}
          placeholder="for example: Production incident escalation path"
          className="w-full rounded-lg border border-line-strong bg-transparent px-3 py-1.5 text-sm outline-none focus:border-thread-solid"
        />
        <textarea
          value={text}
          maxLength={2000}
          aria-label="The agreement itself"
          onChange={(e) => setText(e.target.value)}
          rows={2}
          placeholder="The agreement itself…"
          className="w-full rounded-lg border border-line-strong bg-transparent px-3 py-1.5 text-sm outline-none focus:border-thread-solid"
        />
        <div className="flex items-center gap-2 text-xs text-ink-2">
          <label htmlFor="charter-review-by">
            Review by — charter entries go stale like decisions do:
          </label>
          <input
            id="charter-review-by"
            name="charter-review-by"
            type="date"
            value={reviewBy}
            onChange={(e) => setReviewBy(e.target.value)}
            className="rounded-lg border border-line-strong bg-transparent px-2 py-1 text-sm outline-none focus:border-thread-solid"
          />
        </div>
        <button
          onClick={add}
          disabled={busy || !title.trim() || !text.trim() || !reviewBy}
          className="rounded-lg bg-thread-solid px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-40"
        >
          Record charter entry
        </button>
      </div>

      <ul className="space-y-3">
        {(decisions ?? []).map((d) => (
          <li
            key={d.id}
            className="rounded-xl border border-line bg-card p-4 text-sm shadow-card"
          >
            <div className="mb-1 flex items-center justify-between">
              <span
                id={`charter-entry-${d.id}`}
                tabIndex={-1}
                className="font-semibold outline-none"
              >
                {d.title}
              </span>
              <span
                className={
                  "text-xs " +
                  (d.status === "stale" ? "text-weld" : "text-ink-3")
                }
              >
                {d.status === "stale"
                  ? "⚠ stale — reconfirm or supersede"
                  : d.status}
                {d.review_by ? ` · review by ${d.review_by}` : ""}
              </span>
            </div>
            <p className="text-ink-2">{d.decision}</p>
            <p className="mt-1 text-xs text-ink-3">
              by {d.decided_by || "unrecorded"} · {d.created_at.slice(0, 10)}
              {/* named only in the widened slice: in the charter slice every
                  row is a charter entry, and a badge repeating that on all of
                  them carries no information */}
              {showAll ? ` · ${d.category === "charter" ? "charter" : "decision"}` : ""}
            </p>
            {d.status !== "superseded" && (
              <div className="mt-2 flex gap-2 text-xs">
                {d.status === "stale" && (
                  <button
                    onClick={async () => {
                      try {
                        await api(`/api/decisions/${d.id}/reconfirm`, {
                          method: "POST",
                          body: JSON.stringify({}),
                        });
                        load();
                      } catch (e) {
                        reportStatus(actionError(e));
                      }
                    }}
                    className="rounded bg-ok/15 px-2 py-1 font-medium text-ok hover:bg-ok/20"
                  >
                    still true — reconfirm
                  </button>
                )}
                {superseding === d.id ? null : (
                  <button
                    id={`supersede-${d.id}`}
                    onClick={() => {
                      setSuperseding(Number(d.id));
                      setNewText("");
                    }}
                    className="rounded bg-raised px-2 py-1 text-ink-2 hover:bg-line"
                  >
                    supersede…
                  </button>
                )}
              </div>
            )}
            {superseding === d.id && (
              <div
                className="mt-2 space-y-1.5"
                onKeyDown={(e) => e.key === "Escape" && closeSupersede(d.id)}
              >
                <textarea
                  autoFocus
                  name="supersede-text"
                  aria-label="The replacement agreement"
                  value={newText}
                  maxLength={2000}
                  onChange={(e) => setNewText(e.target.value)}
                  rows={2}
                  placeholder="The replacement agreement — the old one stays in the chain"
                  className="w-full rounded-lg border border-line-strong bg-transparent px-2 py-1.5 text-sm outline-none focus:border-thread-solid"
                />
                <div className="flex gap-2 text-xs">
                  <button
                    disabled={busy || !newText.trim()}
                    onClick={async () => {
                      if (busy) return;
                      setBusy(true);
                      try {
                        await api(`/api/decisions/${d.id}/supersede`, {
                          method: "POST",
                          body: JSON.stringify({
                            title: d.title,
                            decision: newText.trim(),
                          }),
                        });
                        setSuperseding(null);
                        load();
                        // trigger button disappears (entry is superseded) —
                        // land focus on the entry itself
                        setTimeout(
                          () =>
                            document
                              .getElementById(`charter-entry-${d.id}`)
                              ?.focus(),
                          0,
                        );
                      } catch (e) {
                        reportStatus(actionError(e));
                      } finally {
                        setBusy(false);
                      }
                    }}
                    className="rounded bg-thread-solid px-2 py-1 font-medium text-white hover:opacity-90 disabled:opacity-40"
                  >
                    Replace it
                  </button>
                  <button
                    onClick={() => closeSupersede(d.id)}
                    className="text-ink-3 hover:text-ink"
                  >
                    cancel
                  </button>
                </div>
              </div>
            )}
          </li>
        ))}
        {/* an <li>, like the empty state directly below: a <p> as a direct
            child of <ul> is invalid and axe reports it (1.3.1) */}
        {decisions === null && !error && (
          <li>
            <p className="text-sm text-ink-3">Loading…</p>
          </li>
        )}
        {decisions !== null && decisions.length === 0 && !error && (
          <li>
            <EmptyState>
              {showAll
                ? "No decisions recorded yet. Capture one with ‘decision:’ in quick capture."
                : "No charter entries yet. Start with: who owns what, how we escalate, what quality bar we hold."}
            </EmptyState>
          </li>
        )}
      </ul>
    </main>
  );
}
