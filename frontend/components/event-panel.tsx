"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { draftOf, EventForm, type EventFields, fieldsOf, patchOf } from "@/components/event-form";
import Link from "next/link";

import { ReceiptLine } from "@/components/receipt";
import { StakeholderBrief } from "@/components/stakeholder-brief";
import { PeekLink } from "@/components/task-peek";
import { VisibilityBadge } from "@/components/visibility-picker";
import { actionError, api, loadError } from "@/lib/api";
import { addDays, dayLabel } from "@/lib/calendar";
import { type EntityRef, refHref } from "@/lib/entity-ref";
import { reportStatus } from "@/lib/status";

/** GET /api/events/{id}: the row plus its times on the team clock. */
export type CalendarEvent = {
  id: number;
  title: string;
  starts_at: string;
  ends_at: string | null;
  starts_local: string;
  ends_local: string | null;
  description: string;
  attendees: string;
  agenda: string;
  visibility: string;
  crew_id: number | null;
  engagement_id: number | null;
  outcome_status: string;
  // the agenda's `question #12` references this reader may open
  // (services/refs.py::readable_refs), from GET /api/events/{id} only
  agenda_refs?: EntityRef[];
};

/** GET /api/events/{id}/items: per kind, the linked rows the reader may
 *  read (services/schedule.py::event_items). */
type Linked = Record<string, { id: number; title: string }[]>;

// the kinds of services/schedule.py::LINKED, in the order a meeting makes them
const KINDS: [string, string][] = [
  ["decision", "Decision"],
  ["task", "Task"],
  ["question", "Question"],
  ["blocker", "Blocker"],
  ["promise", "Promise"],
  ["intake", "Request"],
  ["note", "Note"],
];

function LinkedItem({ kind, id, title }: { kind: string; id: number; title: string }) {
  const text = title || `#${id}`;
  if (kind === "task") return <PeekLink taskId={id}>{text}</PeekLink>;
  // a note opens alone on /notes (app/notes/page.tsx); lib/entity-ref.ts
  // knows every other kind
  const href = kind === "note" ? `/notes?note=${id}` : refHref({ entity: kind, id });
  return href ? (
    <Link href={href} className="underline decoration-line-strong hover:text-ink">
      {text}
    </Link>
  ) : (
    <span>{text}</span>
  );
}

/** "Thursday, October 1, 10:00 to 11:00", on the team clock. An all-day end
 *  is exclusive (lib/calendar.ts::eventDays). */
function whenText(e: Pick<CalendarEvent, "starts_at" | "ends_at" | "starts_local" | "ends_local">): string {
  if (e.starts_at.length === 10) {
    const last = e.ends_at && e.ends_at > e.starts_at ? addDays(e.ends_at, -1) : e.starts_at;
    return last === e.starts_at
      ? `All day, ${dayLabel(e.starts_at)}`
      : `All day, ${dayLabel(e.starts_at)} to ${dayLabel(last)}`;
  }
  const day = e.starts_local.slice(0, 10);
  const start = e.starts_local.slice(11, 16);
  if (!e.ends_local) return `${dayLabel(day)}, ${start}`;
  const endDay = e.ends_local.slice(0, 10);
  const end = e.ends_local.slice(11, 16);
  return endDay === day
    ? `${dayLabel(day)}, ${start} to ${end}`
    : `${dayLabel(day)}, ${start} to ${dayLabel(endDay)}, ${end}`;
}

export function EventPanel({
  eventId,
  onClose,
  onChanged,
  onLoaded,
}: {
  eventId: number;
  onClose: () => void;
  onChanged: () => void;
  onLoaded?: (e: CalendarEvent) => void;
}) {
  const [loaded, setLoaded] = useState<{ id: number; event?: CalendarEvent; error?: string } | null>(
    null,
  );
  const [deleting, setDeleting] = useState(false);
  const [editing, setEditing] = useState(false);
  const [linked, setLinked] = useState<{ id: number; items?: Linked; error?: string } | null>(
    null,
  );
  const [linkKind, setLinkKind] = useState("decision");
  const [linkId, setLinkId] = useState("");
  const asideRef = useRef<HTMLElement>(null);
  // Focus moves after React commits, never on a timer: the Edit button is
  // not in the DOM until the form that replaced it unmounts.
  const pendingFocus = useRef<string | null>(null);
  useEffect(() => {
    const id = pendingFocus.current;
    if (!id) return;
    pendingFocus.current = null;
    document.getElementById(id)?.focus();
  });
  const closeRef = useRef<HTMLButtonElement>(null);
  // where focus goes back to: the control that opened the panel, or the
  // page when the panel opened from a link
  const opener = useRef<Element | null>(null);
  // refs, so the modal effect below runs once per open: keyed on the
  // callback, it re-runs on every render of a parent that passes an inline
  // arrow, and each run moves focus back to Close
  const onLoadedRef = useRef(onLoaded);
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onLoadedRef.current = onLoaded;
    onCloseRef.current = onClose;
  });

  const load = useCallback(() => {
    let live = true;
    api<CalendarEvent>(`/api/events/${eventId}`)
      .then((event) => {
        if (!live) return;
        setLoaded({ id: eventId, event });
        onLoadedRef.current?.(event);
      })
      .catch((e) => live && setLoaded({ id: eventId, error: loadError(e) }));
    return () => {
      live = false;
    };
  }, [eventId]);
  useEffect(load, [load]);

  const loadLinked = useCallback(() => {
    let live = true;
    api<Linked>(`/api/events/${eventId}/items`)
      .then((items) => live && setLinked({ id: eventId, items }))
      .catch((e) => live && setLinked({ id: eventId, error: loadError(e) }));
    return () => {
      live = false;
    };
  }, [eventId]);
  useEffect(loadLinked, [loadLinked]);

  useEffect(() => {
    opener.current = document.activeElement;
    // aria-modal prunes the screen reader's buffer, not the Tab order: the
    // siblings go inert, as in components/task-peek.tsx, whose comments hold
    // the reasons. The panel is a body child (the portal below), so the loop
    // leaves it alone. The status nodes stay live (status-region.tsx).
    const others = [...document.body.children].filter(
      (el) => !el.contains(closeRef.current) && !el.hasAttribute("data-status-region"),
    ) as HTMLElement[];
    const assert = () => others.forEach((el) => el.setAttribute("inert", ""));
    assert();
    closeRef.current?.focus();
    // Only while focus is in this panel: a task opened from it (PeekLink)
    // stacks the task peek on top, and Escape there must close that alone.
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && asideRef.current?.contains(document.activeElement))
        onCloseRef.current();
    };
    document.addEventListener("keydown", onKey);
    // components/task-peek.tsx gives back inert on every body child when it
    // closes, this panel's siblings included, and says so there
    window.addEventListener("skein-peek-close", assert);
    return () => {
      others.forEach((el) => el.removeAttribute("inert"));
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("skein-peek-close", assert);
      const back = opener.current;
      // the opener can be gone: a deleted event's button leaves the grid
      if (back instanceof HTMLElement && back.isConnected && back !== document.body) back.focus();
      else document.getElementById("content")?.focus();
    };
  }, []);

  const event = loaded?.id === eventId ? loaded.event : undefined;
  const error = loaded?.id === eventId ? loaded.error : undefined;

  const save = async (fields: EventFields) => {
    if (!event) return false;
    const patch = patchOf(fieldsOf(draftOf(event)), fields);
    if (Object.keys(patch).length) {
      try {
        await api(`/api/events/${eventId}`, { method: "PATCH", body: JSON.stringify(patch) });
      } catch (e) {
        reportStatus(actionError(e));
        return false;
      }
      reportStatus("Event saved.", "confirmation");
      load();
      onChanged();
    }
    setEditing(false);
    pendingFocus.current = "event-edit";
    return true;
  };

  const link = async () => {
    const id = Number(linkId);
    if (!Number.isInteger(id) || id <= 0) {
      reportStatus("The record number is not valid. Type the number after the #.");
      return;
    }
    try {
      await api(`/api/events/${eventId}/links`, {
        method: "POST",
        body: JSON.stringify({ kind: linkKind, item_id: id }),
      });
    } catch (e) {
      reportStatus(actionError(e));
      return;
    }
    setLinkId("");
    reportStatus("Record linked to this meeting.", "confirmation");
    loadLinked();
  };

  const unlink = async (kind: string, id: number) => {
    try {
      await api(`/api/events/${eventId}/links/${kind}/${id}`, { method: "DELETE" });
    } catch (e) {
      reportStatus(actionError(e));
      return;
    }
    reportStatus("Link removed. The record stays.", "confirmation");
    pendingFocus.current = "event-link-id";
    loadLinked();
  };

  const remove = async () => {
    try {
      await api(`/api/events/${eventId}`, { method: "DELETE" });
      reportStatus("Event deleted.", "confirmation");
      onChanged();
      onClose();
    } catch (e) {
      reportStatus(actionError(e));
    }
  };

  return createPortal(
    <div
      className="fixed inset-0 z-40 flex justify-end"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div aria-hidden className="absolute inset-0 bg-ink/20" />
      <aside
        ref={asideRef}
        role="dialog"
        aria-modal="true"
        aria-label={event ? `Event: ${event.title}` : "Event"}
        className="relative flex h-full w-full max-w-md flex-col gap-3 overflow-y-auto border-l border-line bg-card p-4 shadow-card"
      >
        <div className="flex items-start justify-between gap-2">
          {/* live: focus moves to Close before the fetch answers, and the
              title that arrives later must still be spoken */}
          <h2 aria-live="polite" className="text-sm font-medium text-ink [overflow-wrap:anywhere]">
            {event?.title ?? (error ? "Not available" : "Loading…")}
          </h2>
          <button
            ref={closeRef}
            onClick={onClose}
            aria-label="Close the event panel"
            className="rounded-lg bg-raised px-2 py-0.5 text-xs text-ink-2 hover:bg-line"
          >
            Close
          </button>
        </div>
        {error && <p className="text-sm text-danger">{error}</p>}
        {event && editing && (
          <EventForm
            initial={draftOf(event)}
            idPrefix="event-edit-form"
            submitLabel="Save event"
            withTier={false}
            onSubmit={save}
            onCancel={() => {
              setEditing(false);
              pendingFocus.current = "event-edit";
            }}
          />
        )}
        {event && !editing && (
          <>
            <div className="flex flex-wrap items-center gap-2 text-sm text-ink-2">
              <span>{whenText(event)}</span>
              <VisibilityBadge visibility={event.visibility} crewId={event.crew_id ?? undefined} />
            </div>
            {event.attendees && (
              <p className="text-sm text-ink-2 [overflow-wrap:anywhere]">
                <span className="text-ink-3">With </span>
                {event.attendees}
              </p>
            )}
            {event.description && (
              <p className="whitespace-pre-wrap text-sm text-ink [overflow-wrap:anywhere]">
                {event.description}
              </p>
            )}
            {event.agenda && (
              <section>
                <h3 className="text-xs font-medium text-ink-3">Agenda</h3>
                <p className="mt-0.5 whitespace-pre-wrap text-sm text-ink [overflow-wrap:anywhere]">
                  <ReceiptLine receipt={{ message: event.agenda, refs: event.agenda_refs ?? [] }} />
                </p>
              </section>
            )}
            <div>
              <StakeholderBrief eventId={event.id} />
            </div>
            <section aria-labelledby="event-linked-title">
              <h3 id="event-linked-title" className="text-xs font-medium text-ink-3">
                From this meeting
              </h3>
              {linked?.id !== eventId ? (
                <p className="mt-0.5 text-xs text-ink-3">Loading…</p>
              ) : linked.error ? (
                <p className="mt-0.5 text-xs text-danger">{linked.error}</p>
              ) : KINDS.every(([kind]) => !linked.items?.[kind]?.length) ? (
                <p className="mt-0.5 text-xs text-ink-3">No records that you can read link to this meeting.</p>
              ) : (
                <ul className="mt-0.5 space-y-0.5 text-sm text-ink-2">
                  {KINDS.flatMap(([kind, label]) =>
                    (linked.items?.[kind] ?? []).map((item) => (
                      <li key={`${kind}-${item.id}`} className="flex items-baseline gap-2 [overflow-wrap:anywhere]">
                        <span className="shrink-0 text-xs text-ink-3">{label}</span>
                        <span className="min-w-0 flex-1">
                          <LinkedItem kind={kind} id={item.id} title={item.title} />
                        </span>
                        <button
                          onClick={() => unlink(kind, item.id)}
                          aria-label={`Unlink ${label.toLowerCase()} #${item.id} from this meeting`}
                          className="min-h-6 min-w-6 shrink-0 rounded px-1 text-xs text-ink-3 hover:bg-raised hover:text-ink"
                        >
                          unlink
                        </button>
                      </li>
                    )),
                  )}
                </ul>
              )}
              <div className="mt-2 flex flex-wrap items-end gap-2 text-xs">
                <Link
                  href={`/ingest?event=${event.id}`}
                  className="rounded bg-raised px-2 py-1 text-ink-2 hover:bg-line"
                >
                  Paste notes for this meeting
                </Link>
                <form
                  className="flex flex-wrap items-end gap-1.5"
                  onSubmit={(e) => {
                    e.preventDefault();
                    link();
                  }}
                >
                  <label className="text-ink-3">
                    Kind
                    <select
                      value={linkKind}
                      onChange={(e) => setLinkKind(e.target.value)}
                      className="mt-0.5 block rounded border border-line-strong bg-transparent px-1 py-0.5 text-ink"
                    >
                      {KINDS.map(([kind, label]) => (
                        <option key={kind} value={kind}>
                          {label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="text-ink-3">
                    Record number
                    <input
                      id="event-link-id"
                      inputMode="numeric"
                      value={linkId}
                      onChange={(e) => setLinkId(e.target.value.replace(/[^0-9]/g, ""))}
                      className="mt-0.5 block w-20 rounded border border-line-strong bg-transparent px-1 py-0.5 text-ink"
                    />
                  </label>
                  <button
                    type="submit"
                    className="rounded bg-raised px-2 py-1 text-ink-2 hover:bg-line"
                  >
                    Link a record
                  </button>
                </form>
              </div>
            </section>
            <div className="mt-auto flex flex-wrap items-start gap-2 border-t border-line pt-3">
              <button
                id="event-edit"
                onClick={() => {
                  setDeleting(false);
                  setEditing(true);
                }}
                aria-label={`Edit event: ${event.title}`}
                className="min-h-6 min-w-6 rounded bg-raised px-2 py-0.5 text-xs text-ink-2 hover:bg-line"
              >
                edit…
              </button>
              {deleting ? (
                <span
                  onKeyDown={(e) => {
                    if (e.key !== "Escape") return;
                    // Escape here cancels the deletion, not the panel
                    e.stopPropagation();
                    e.nativeEvent.stopImmediatePropagation();
                    setDeleting(false);
                  }}
                  className="flex max-w-sm flex-col gap-1 text-xs"
                >
                  <span id="event-delete-consequence">
                    Delete this event? It leaves the calendar and the calendar feed.
                    Records that came out of it stay, without the link. An activity
                    record of the deletion will stay.
                  </span>
                  <span className="flex gap-3 md:gap-1.5">
                    <button
                      autoFocus
                      aria-describedby="event-delete-consequence"
                      onClick={remove}
                      className="rounded bg-danger-solid px-2 py-1.5 font-medium text-white hover:opacity-90 md:py-0.5"
                    >
                      Delete event
                    </button>
                    <button
                      onClick={() => setDeleting(false)}
                      className="rounded px-2 py-0.5 text-ink-3 hover:text-ink"
                    >
                      Cancel deletion
                    </button>
                  </span>
                </span>
              ) : (
                <button
                  onClick={() => setDeleting(true)}
                  aria-label={`Delete event: ${event.title}`}
                  className="min-h-6 min-w-6 rounded bg-raised px-2 py-0.5 text-xs text-danger hover:bg-line"
                >
                  delete…
                </button>
              )}
            </div>
          </>
        )}
      </aside>
    </div>,
    document.body,
  );
}
