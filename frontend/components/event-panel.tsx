"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { draftOf, EventForm, type EventFields, fieldsOf, patchOf } from "@/components/event-form";
import { StakeholderBrief } from "@/components/stakeholder-brief";
import { VisibilityBadge } from "@/components/visibility-picker";
import { actionError, api, loadError } from "@/lib/api";
import { addDays, dayLabel } from "@/lib/calendar";
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
};

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

  useEffect(() => {
    opener.current = document.activeElement;
    // aria-modal prunes the screen reader's buffer, not the Tab order: the
    // siblings go inert, as in components/task-peek.tsx, whose comments hold
    // the reasons. The panel is a body child (the portal below), so the loop
    // leaves it alone. The status nodes stay live (status-region.tsx).
    const others = [...document.body.children].filter(
      (el) => !el.contains(closeRef.current) && !el.hasAttribute("data-status-region"),
    ) as HTMLElement[];
    others.forEach((el) => el.setAttribute("inert", ""));
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCloseRef.current();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      others.forEach((el) => el.removeAttribute("inert"));
      document.removeEventListener("keydown", onKey);
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
                  {event.agenda}
                </p>
              </section>
            )}
            <div>
              <StakeholderBrief eventId={event.id} />
            </div>
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
                    Delete this event? It leaves the calendar and the calendar feed. An
                    activity record of the deletion will stay.
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
