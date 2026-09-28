"use client";

import { useState } from "react";

import { VisibilityPicker } from "@/components/visibility-picker";
import { isTierChoice, ONLY_YOU, ROSTER, useRememberedAudience, useStrongIdentity } from "@/lib/audience";
import { addDays } from "@/lib/calendar";
import { reportStatus } from "@/lib/status";

/** What the form sends: the fields of POST /api/events (EventIn in
 *  routes/api.py) that a person types. Times are on the team clock with no
 *  offset, which the server reads as the team's zone (schedule.py::_canon). */
export type EventFields = {
  title: string;
  starts_at: string;
  ends_at: string;
  description: string;
  attendees: string;
  agenda: string;
};

export type EventDraft = {
  title: string;
  allDay: boolean;
  firstDay: string;
  lastDay: string;
  starts: string;
  ends: string;
  description: string;
  attendees: string;
  agenda: string;
};

export function blankDraft(day = ""): EventDraft {
  return {
    title: "",
    allDay: false,
    firstDay: day,
    lastDay: "",
    starts: day ? `${day}T09:00` : "",
    ends: "",
    description: "",
    attendees: "",
    agenda: "",
  };
}

function fieldsOf(d: EventDraft): EventFields {
  const common = {
    title: d.title.trim(),
    description: d.description,
    attendees: d.attendees,
    agenda: d.agenda,
  };
  if (d.allDay) {
    const last = d.lastDay && d.lastDay > d.firstDay ? d.lastDay : "";
    return { ...common, starts_at: d.firstDay, ends_at: last ? addDays(last, 1) : "" };
  }
  return { ...common, starts_at: d.starts, ends_at: d.ends };
}

/** What stops the form, in words, or "". The server refuses the same cases
 *  (schedule.py), and saying so here keeps the reader's typing. */
function problem(d: EventDraft): string {
  if (!d.title.trim()) return "The title is empty. Type a title to save the event.";
  if (d.allDay) {
    if (!d.firstDay) return "The first day is empty. Choose a day to save the event.";
    if (d.lastDay && d.lastDay < d.firstDay)
      return "The last day is before the first day. Choose a later last day.";
    return "";
  }
  if (!d.starts) return "The start is empty. Choose a start to save the event.";
  if (d.ends && d.ends <= d.starts) return "The end is not after the start. Choose a later end.";
  return "";
}

const field =
  "mt-0.5 block w-full rounded-lg border border-line-strong bg-transparent px-2 py-1 text-sm text-ink outline-none focus:border-thread-solid";

export function EventForm({
  initial,
  idPrefix,
  submitLabel,
  withTier,
  onSubmit,
  onCancel,
}: {
  initial: EventDraft;
  idPrefix: string;
  submitLabel: string;
  // false on an edit: an update never changes the tier (docs/intent/calendar.md, D8)
  withTier: boolean;
  onSubmit: (fields: EventFields & Partial<{ visibility: string; crew_id: number }>) => Promise<boolean>;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState(initial);
  const [busy, setBusy] = useState(false);
  const strong = useStrongIdentity();
  // "only you" first for a signed-in person, as every personal record starts
  // (routes/api.py::_personal_default). A weak identity reads no private
  // row, so it would file a meeting its own author cannot open.
  const [tier, setTier] = useRememberedAudience(
    "event",
    strong ? ONLY_YOU : ROSTER,
    (v) => isTierChoice(v) && (strong || (v as { visibility: string }).visibility !== "private"),
  );
  const set = (patch: Partial<EventDraft>) => setDraft((cur) => ({ ...cur, ...patch }));
  const blocked = problem(draft);

  return (
    <form
      className="space-y-2"
      onSubmit={async (e) => {
        e.preventDefault();
        if (busy) return;
        if (blocked) {
          reportStatus(blocked);
          return;
        }
        setBusy(true);
        const ok = await onSubmit({
          ...fieldsOf(draft),
          ...(withTier ? { visibility: tier.visibility, crew_id: tier.crew_id } : {}),
        });
        setBusy(false);
        if (ok) setDraft(initial);
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape" && !e.nativeEvent.isComposing) onCancel();
      }}
    >
      <label className="block text-xs text-ink-3">
        Title
        <input
          id={`${idPrefix}-title`}
          autoFocus
          value={draft.title}
          maxLength={200}
          onChange={(e) => set({ title: e.target.value })}
          className={field}
        />
      </label>
      <label className="flex items-center gap-2 text-xs text-ink-2">
        <input
          type="checkbox"
          checked={draft.allDay}
          onChange={(e) =>
            set({
              allDay: e.target.checked,
              firstDay: draft.firstDay || draft.starts.slice(0, 10),
              starts: draft.starts || (draft.firstDay ? `${draft.firstDay}T09:00` : ""),
            })
          }
        />
        All day
      </label>
      {draft.allDay ? (
        <div className="flex flex-wrap gap-2">
          <label className="min-w-0 flex-1 basis-36 text-xs text-ink-3">
            First day
            <input
              type="date"
              value={draft.firstDay}
              onChange={(e) => set({ firstDay: e.target.value })}
              className={field}
            />
          </label>
          <label className="min-w-0 flex-1 basis-36 text-xs text-ink-3">
            Last day (optional)
            <input
              type="date"
              value={draft.lastDay}
              min={draft.firstDay || undefined}
              onChange={(e) => set({ lastDay: e.target.value })}
              className={field}
            />
          </label>
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          <label className="min-w-0 flex-1 basis-44 text-xs text-ink-3">
            Starts (team clock)
            <input
              type="datetime-local"
              value={draft.starts}
              onChange={(e) => set({ starts: e.target.value })}
              className={field}
            />
          </label>
          <label className="min-w-0 flex-1 basis-44 text-xs text-ink-3">
            Ends (optional)
            <input
              type="datetime-local"
              value={draft.ends}
              min={draft.starts || undefined}
              onChange={(e) => set({ ends: e.target.value })}
              className={field}
            />
          </label>
        </div>
      )}
      <label className="block text-xs text-ink-3">
        Attendees (names, separated by commas)
        <input
          value={draft.attendees}
          maxLength={500}
          onChange={(e) => set({ attendees: e.target.value })}
          className={field}
        />
      </label>
      <label className="block text-xs text-ink-3">
        Description
        <textarea
          rows={2}
          value={draft.description}
          maxLength={4000}
          onChange={(e) => set({ description: e.target.value })}
          className={field}
        />
      </label>
      <label className="block text-xs text-ink-3">
        Agenda
        <textarea
          rows={3}
          value={draft.agenda}
          maxLength={2000}
          onChange={(e) => set({ agenda: e.target.value })}
          className={field}
        />
      </label>
      {withTier && (
        <VisibilityPicker value={tier} onChange={setTier} label="event" allowPrivate={strong} />
      )}
      {blocked && draft.title && (
        <p id={`${idPrefix}-blocked`} className="text-xs text-danger">
          {blocked}
        </p>
      )}
      <div className="flex gap-2">
        <button
          type="submit"
          aria-disabled={busy || Boolean(blocked)}
          aria-describedby={blocked && draft.title ? `${idPrefix}-blocked` : undefined}
          className="rounded bg-thread-solid px-2 py-1 text-xs font-medium text-white hover:opacity-90 aria-disabled:opacity-40"
        >
          {submitLabel}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded px-2 py-1 text-xs text-ink-3 hover:text-ink"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}
