"use client";

import { useState } from "react";

import { actionError, api } from "@/lib/api";

/** What is open with the outside people attending one meeting.
 *
 *  Lazy: the request fires when the reader asks, not on every My Day load. A
 *  meeting with no outside attendee has no threads, and most do not.
 */
export function StakeholderBrief({ eventId }: { eventId: number }) {
  const [open, setOpen] = useState(false);
  // the shape services/stakeholders.py::open_threads returns: one row per
  // party, each carrying its items. The planning cockpit reads the same
  // service and types it this way (app/planning/page.tsx)
  const [threads, setThreads] = useState<
    | { party: string; items: { kind: string; text: string; when: string }[] }[]
    | null
  >(null);
  // a THIRD state. `[]` is what a meeting with no outside attendee returns,
  // so a failure written as `[]` renders "nothing is open" - a claim about the
  // world manufactured from a transport failure, read by somebody walking into
  // the room.
  const [err, setErr] = useState("");

  const show = async () => {
    setOpen(true);
    if (threads !== null) return; // `[]` is a real answer, not a cache miss
    setErr("");
    try {
      const r = await api<{ threads: typeof threads }>(
        `/api/events/${eventId}/stakeholders`,
      );
      setThreads(r.threads ?? []);
      setErr("");
    } catch (e) {
      setErr(actionError(e));
    }
  };

  return (
    // a DIV, not a span: it holds a list, and phrasing content cannot. Its
    // parent must be a div for the same reason (My Day's events list in
    // app/page.tsx, and components/event-panel.tsx).
    <div className="ml-1.5 text-xs text-ink-3">
      {/* the trigger STAYS mounted and toggles. Unmounting it on activation
          dropped a keyboard reader's focus to <body>, and left a failed fetch
          with no control at all - no retry and no way back. Collapsing clears
          nothing, so re-opening after a failure refetches (threads is still
          null). */}
      <button
        onClick={() => (open ? setOpen(false) : show())}
        aria-expanded={open}
        className="min-h-6 min-w-6 rounded bg-raised px-1.5 py-px text-[10px] text-ink-3 hover:bg-line"
      >
        {open ? "hide" : "what is open?"}
      </button>
      {!open ? null : err ? (
        <p className="text-danger">{err}</p>
      ) : threads === null ? (
        <p>Loading…</p>
      ) : threads.length === 0 ? (
        <p>Nothing is open with anyone outside the team in this meeting.</p>
      ) : (
        <ul className="mt-0.5 space-y-0.5">
          {threads.map((t) => (
            <li key={t.party}>
              {t.party}:{" "}
              {t.items
                .map((i) => i.text + (i.when ? ` (${i.when})` : ""))
                .join("; ")}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
