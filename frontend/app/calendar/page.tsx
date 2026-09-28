"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";

import { Card } from "@/components/card";
import { blankDraft, EventForm, type EventFields } from "@/components/event-form";
import { type CalendarEvent, EventPanel } from "@/components/event-panel";
import { PeekLink } from "@/components/task-peek";
import { WeakIdentityNotice } from "@/components/weak-identity-notice";
import { actionError, api, loadError } from "@/lib/api";
import {
  dayLabel,
  eventDays,
  monthGrid,
  monthLabel,
  monthOf,
  shiftMonth,
  spanDays,
  WEEKDAYS,
} from "@/lib/calendar";
import { reportStatus } from "@/lib/status";
import { emptyState } from "@/lib/whimsy";

type Task = { id: number; title: string; due_date: string; assignee: string };
type Milestone = { id: number; title: string; due_date: string };
type Promised = { id: number; promise: string; to_whom: string; due_date: string; direction: string };
// a window the reader cannot read arrives with no id, kind "away" and no
// note (services/schedule.py::calendar_range)
type Away = { id?: number; person: string; kind: string; starts_on: string; ends_on: string };

/** GET /api/calendar. */
type CalendarData = {
  start: string;
  end: string;
  today: string;
  events: CalendarEvent[];
  tasks: Task[];
  milestones: Milestone[];
  promises: Promised[];
  time_away: Away[];
  truncated: string[];
};

const KINDS = [
  ["events", "Meetings"],
  ["tasks", "Tasks"],
  ["milestones", "Milestones"],
  ["promises", "Promises"],
  ["time_away", "Time away"],
] as const;
type Kind = (typeof KINDS)[number][0];

const AWAY_WORD: Record<string, string> = {
  pto: "time off",
  oncall: "on call",
  focus: "focus time",
  away: "away",
};

// `title`: the full text a cell truncates, for a pointer that rests on it
type Item = { key: string; kind: Kind; order: string; title: string; node: React.ReactNode };

/** "meetings", "meetings and tasks", "meetings, tasks and time away". */
function listed(words: string[]): string {
  return words.length < 2 ? words.join("") : `${words.slice(0, -1).join(", ")} and ${words.at(-1)}`;
}

/** Reports `?event=<id>`, or 0. In its own Suspense boundary, as
 *  app/notes/page.tsx explains for `?note=`. */
function EventParam({ onChange }: { onChange: (id: number) => void }) {
  const raw = useSearchParams().get("event");
  const id = Number(raw);
  const event = raw && Number.isInteger(id) && id > 0 ? id : 0;
  useEffect(() => onChange(event), [event, onChange]);
  return null;
}

/** The browser's month, used only for the first request. The answer's
 *  `today` is the team day, and the page moves to its month once (below). */
function browserMonth() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

export default function CalendarPage() {
  const [month, setMonth] = useState(browserMonth);
  const [mine, setMine] = useState(true);
  const [shown, setShown] = useState<Record<Kind, boolean>>({
    events: true,
    tasks: true,
    milestones: true,
    promises: true,
    time_away: true,
  });
  const [data, setData] = useState<{ key: string; value: CalendarData } | null>(null);
  const [failure, setFailure] = useState<{ key: string; message: string } | null>(null);
  const [eventId, setEventId] = useState(0);
  const [adding, setAdding] = useState<string | null>(null);
  const followToday = useRef(true);
  const generation = useRef(0);
  // Focus moves after React commits, never on a timer: "Add an event" is
  // back in reach only once the form has unmounted.
  const pendingFocus = useRef<string | null>(null);
  useEffect(() => {
    const id = pendingFocus.current;
    if (!id) return;
    pendingFocus.current = null;
    document.getElementById(id)?.focus();
  });

  const days = monthGrid(month);
  const key = `${month}|${mine}`;

  const load = useCallback(() => {
    const current = ++generation.current;
    const grid = monthGrid(month);
    const params = new URLSearchParams({ start: grid[0], end: grid[grid.length - 1] });
    if (mine) params.set("mine", "true");
    api<CalendarData>(`/api/calendar?${params}`)
      .then((value) => {
        if (current !== generation.current) return;
        if (followToday.current) {
          followToday.current = false;
          // the browser's month is not the team's near a month boundary
          if (monthOf(value.today) !== month) {
            setMonth(monthOf(value.today));
            return;
          }
        }
        setData({ key: `${month}|${mine}`, value });
        setFailure(null);
      })
      .catch((e) => {
        if (current === generation.current) setFailure({ key: `${month}|${mine}`, message: loadError(e) });
      });
  }, [month, mine]);
  useEffect(load, [load]);

  // GET /api/calendar answers every reader, so the page ties its own
  // field-guide card. A failed mark leaves the card untied, nothing more.
  useEffect(() => {
    api("/api/field-guide/calendar", { method: "POST" }).catch(() => {});
  }, []);

  const onParam = useCallback((id: number) => setEventId(id), []);

  // The entry a click here pushed carries the event it opened, as
  // components/task-peek.tsx marks its own. Close goes Back only to leave
  // that entry: an agenda link or a search hit arrives on an entry this page
  // did not push, and Back from it reopens the event read before.
  const openEvent = (id: number) => {
    window.history.pushState({ skeinEvent: id }, "", `/calendar?event=${id}`);
    setEventId(id);
  };

  const closeEvent = useCallback(() => {
    const open = new URLSearchParams(window.location.search).get("event");
    setEventId(0);
    if (open && window.history.state?.skeinEvent === Number(open)) window.history.back();
    else if (open) window.history.replaceState({}, "", "/calendar");
  }, []);

  // A link from search or a receipt names an event that can be in any
  // month. Moving to it shows the reader where it sits.
  const onEventLoaded = useCallback(
    (e: CalendarEvent) => {
      const grid = monthGrid(month);
      const covered = eventDays(e.starts_local, e.ends_local ?? e.ends_at);
      if (!covered.some((d) => d >= grid[0] && d <= grid[grid.length - 1])) {
        followToday.current = false;
        setMonth(monthOf(covered[0]));
      }
    },
    [month],
  );

  const create = async (fields: EventFields & Partial<{ visibility: string; crew_id: number }>) => {
    try {
      await api("/api/events", { method: "POST", body: JSON.stringify(fields) });
    } catch (e) {
      reportStatus(actionError(e));
      return false;
    }
    reportStatus("Event added.", "confirmation");
    setAdding(null);
    pendingFocus.current = "calendar-add";
    followToday.current = false;
    const target = monthOf(fields.starts_at.slice(0, 10));
    if (target !== month) setMonth(target);
    else load();
    return true;
  };

  const current = data?.key === key ? data.value : null;
  const failed = failure?.key === key ? failure.message : null;

  const byDay = new Map<string, Item[]>();
  const put = (day: string, item: Item) => {
    if (!byDay.has(day)) byDay.set(day, []);
    byDay.get(day)!.push(item);
  };
  if (current) {
    if (shown.events)
      for (const e of current.events) {
        const covered = eventDays(e.starts_local, e.ends_local ?? e.ends_at);
        covered.forEach((day, i) => {
          const time = e.starts_at.length > 10 && i === 0 ? `${e.starts_local.slice(11, 16)} ` : "";
          put(day, {
            key: `event-${e.id}`,
            kind: "events",
            title: e.title,
            order: `0${e.starts_at.length > 10 && i === 0 ? e.starts_local.slice(11, 16) : "00:00"}`,
            node: (
              <button
                onClick={() => openEvent(e.id)}
                className="block min-h-6 w-full truncate rounded bg-thread/15 px-1 py-1 text-left text-ink hover:bg-thread/25"
              >
                {time}
                {e.title}
                {covered.length > 1 && i > 0 && <span className="sr-only"> (continued)</span>}
              </button>
            ),
          });
        });
      }
    if (shown.tasks)
      for (const t of current.tasks)
        put(t.due_date, {
          key: `task-${t.id}`,
          kind: "tasks",
          title: `Task due: ${t.title}`,
          order: "1",
          node: (
            <PeekLink taskId={t.id} className="block min-h-6 w-full truncate py-1 text-left text-ink-2 hover:text-ink">
              <span className="text-ink-3">Task due: </span>
              {t.title}
            </PeekLink>
          ),
        });
    if (shown.milestones)
      for (const m of current.milestones)
        put(m.due_date, {
          key: `milestone-${m.id}`,
          kind: "milestones",
          title: `Milestone due: ${m.title}`,
          order: "2",
          node: (
            <Link href={`/dashboard#milestone-${m.id}`} className="block min-h-6 truncate py-1 text-ink-2 hover:text-ink">
              <span className="text-ink-3">Milestone due: </span>
              {m.title}
            </Link>
          ),
        });
    if (shown.promises)
      for (const p of current.promises)
        put(p.due_date, {
          key: `promise-${p.id}`,
          kind: "promises",
          title: `${p.direction === "received" ? "Awaiting" : "Promised"}: ${p.promise}`,
          order: "3",
          node: (
            <Link href={`/portfolio#promise-${p.id}`} className="block min-h-6 truncate py-1 text-ink-2 hover:text-ink">
              {/* the direction is in the word, as in the ICS feed: a received
                  promise under "promised" reads as the reader's own */}
              <span className="text-ink-3">{p.direction === "received" ? "Awaiting: " : "Promised: "}</span>
              {p.promise}
            </Link>
          ),
        });
    if (shown.time_away)
      current.time_away.forEach((a, n) => {
        for (const day of spanDays(a.starts_on, a.ends_on))
          put(day, {
            key: `away-${a.id ?? `shared-${n}`}`,
            kind: "time_away",
            title: `${a.person}: ${AWAY_WORD[a.kind] ?? a.kind}`,
            order: "4",
            node: (
              <span className="block truncate text-ink-3">
                {a.person}: {AWAY_WORD[a.kind] ?? a.kind}
              </span>
            ),
          });
      });
  }
  const anything = [...byDay.keys()].some((d) => d >= days[0] && d <= days[days.length - 1]);

  return (
    <main id="content" tabIndex={-1} className="mx-auto w-full max-w-5xl xl:max-w-6xl p-4 sm:p-6">
      <div className="mb-4">
        <h1 className="font-display text-[24px]/[1.15] font-semibold tracking-[-0.01em] text-ink">
          Calendar
        </h1>
        <p className="mt-0.5 text-sm text-ink-3">
          Meetings, due dates and time away that you can read. Times are on the team clock.
        </p>
      </div>

      <WeakIdentityNotice className="mb-3" />
      <Suspense fallback={null}>
        <EventParam onChange={onParam} />
      </Suspense>

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <button
          onClick={() => {
            followToday.current = false;
            setMonth((m) => shiftMonth(m, -1));
          }}
          aria-label="Previous month"
          className="rounded-lg bg-raised px-2.5 py-1 text-sm text-ink-2 hover:bg-line"
        >
          ‹
        </button>
        <button
          onClick={() => {
            followToday.current = false;
            setMonth(current ? monthOf(current.today) : browserMonth());
          }}
          className="rounded-lg bg-raised px-2.5 py-1 text-sm text-ink-2 hover:bg-line"
        >
          Today
        </button>
        <button
          onClick={() => {
            followToday.current = false;
            setMonth((m) => shiftMonth(m, 1));
          }}
          aria-label="Next month"
          className="rounded-lg bg-raised px-2.5 py-1 text-sm text-ink-2 hover:bg-line"
        >
          ›
        </button>
        <h2 aria-live="polite" className="text-base font-medium text-ink">
          {monthLabel(month)}
        </h2>
        <button
          id="calendar-add"
          onClick={() => setAdding(adding === null ? "" : null)}
          aria-expanded={adding !== null}
          aria-controls="calendar-add-form"
          className="ml-auto rounded-lg bg-thread-solid px-3 py-1 text-sm font-medium text-white hover:opacity-90"
        >
          Add an event
        </button>
      </div>

      {adding !== null && (
        <Card className="mb-3">
          <div id="calendar-add-form">
            <EventForm
              key={adding}
              initial={blankDraft(adding)}
              idPrefix="calendar-add"
              submitLabel="Add event"
              withTier
              onSubmit={create}
              onCancel={() => {
                setAdding(null);
                document.getElementById("calendar-add")?.focus();
              }}
            />
          </div>
        </Card>
      )}

      <fieldset className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-2">
        <legend className="sr-only">Show on the calendar</legend>
        {KINDS.map(([kind, label]) => (
          // min-h-6: the row wraps on a phone, and rows closer than 24px
          // fail the target-size check (e2e/responsive.spec.ts)
          <label key={kind} className="flex min-h-6 items-center gap-1">
            <input
              type="checkbox"
              checked={shown[kind]}
              onChange={(e) => setShown((cur) => ({ ...cur, [kind]: e.target.checked }))}
            />
            {label}
          </label>
        ))}
        <label className="flex min-h-6 items-center gap-1">
          <input
            type="checkbox"
            checked={mine}
            disabled={!shown.tasks}
            onChange={(e) => setMine(e.target.checked)}
          />
          Only my tasks
        </label>
      </fieldset>

      {failed && <p className="mb-3 text-sm text-danger">{failed}</p>}
      {!current && !failed && (
        <Card>
          <p className="text-sm text-ink-3">Loading…</p>
        </Card>
      )}
      {current && current.truncated.length > 0 && (
        <p className="mb-3 text-sm text-ink-2">
          This range holds more{" "}
          {listed(current.truncated.map((k) => KINDS.find(([x]) => x === k)?.[1].toLowerCase() ?? k))}{" "}
          than the calendar can show. Some are not on the grid.
        </p>
      )}
      {current && !failed && !anything && (
        <Card className="mb-3">
          <p className="text-sm text-ink-3">
            No meetings, due dates or time away this month for the kinds you chose.{" "}
            <span className="text-ink-2">{emptyState("calendar")}</span>
          </p>
        </Card>
      )}

      {current && (
        <div>
          <div aria-hidden className="hidden grid-cols-7 gap-px text-center text-xs text-ink-3 sm:grid">
            {WEEKDAYS.map((w) => (
              <span key={w} className="py-1">
                {w}
              </span>
            ))}
          </div>
          <ol className="grid grid-cols-1 gap-2 sm:grid-cols-7 sm:gap-px sm:overflow-hidden sm:rounded-lg sm:border sm:border-line sm:bg-line">
            {days.map((day) => {
              const items = (byDay.get(day) ?? []).sort((a, b) => a.order.localeCompare(b.order));
              const inMonth = monthOf(day) === month;
              const today = day === current.today;
              // a phone lists only the days that hold something, and today
              const phoneHidden = !items.length && !today ? "hidden sm:block" : "";
              return (
                <li
                  key={day}
                  aria-current={today ? "date" : undefined}
                  // one background for every day: a tint on the days of the
                  // next and last month takes text-ink-3 under 4.5:1 in every
                  // pack, and the muted day number already marks them
                  className={`${phoneHidden} min-w-0 rounded-lg border border-line bg-card p-1.5 sm:min-h-24 sm:rounded-none sm:border-0`}
                >
                  <div className="flex items-center justify-between gap-1">
                    <h3 className={`text-xs ${today ? "font-semibold text-thread" : inMonth ? "text-ink-2" : "text-ink-3"}`}>
                      <span className="sm:sr-only">{dayLabel(day)}</span>
                      <span aria-hidden className="hidden sm:inline">
                        {Number(day.slice(8))}
                      </span>
                      {today && <span className="sm:sr-only"> (today)</span>}
                    </h3>
                    <button
                      onClick={() => setAdding(day)}
                      aria-label={`Add an event on ${dayLabel(day)}`}
                      className="min-h-6 min-w-6 rounded text-xs text-ink-3 hover:bg-raised hover:text-ink"
                    >
                      +
                    </button>
                  </div>
                  {items.length > 0 && (
                    <ul className="mt-0.5 space-y-0.5 text-xs">
                      {items.map((item) => (
                        <li key={item.key} title={item.title} className="min-w-0">
                          {item.node}
                        </li>
                      ))}
                    </ul>
                  )}
                </li>
              );
            })}
          </ol>
        </div>
      )}

      {eventId > 0 && (
        // keyed: an agenda link to another meeting swaps the id under an
        // open panel, and unkeyed its delete confirmation carries over and
        // deletes the new one
        <EventPanel
          key={eventId}
          eventId={eventId}
          onClose={closeEvent}
          onChanged={load}
          onLoaded={onEventLoaded}
        />
      )}
    </main>
  );
}
