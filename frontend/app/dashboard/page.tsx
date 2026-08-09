"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { VisibilityBadge } from "@/components/visibility-picker";
import { PeekLink } from "@/components/task-peek";
import { actionError, api, loadError } from "@/lib/api";
import { reportStatus } from "@/lib/status";
import { PersonInput } from "@/components/person-input";
import { SectionTabs } from "@/components/section-tabs";
import { timeAgo } from "@/lib/time";
import { emptyState, loadingLine } from "@/lib/whimsy";

type Pulse = {
  season: { label: string; days_left: number };
  standup_chain: { chain: number; humans: number };
  blocker_speedrun: {
    impact: string;
    cleared: number;
    avg_hours: number;
    best_hours: number;
  }[];
  season_totals: Record<string, number>;
};

type Row = Record<string, string | number | null>;

const STATUS_COLORS: Record<string, string> = {
  planned: "bg-raised text-ink-2",
  todo: "bg-raised text-ink-2",
  in_progress: "bg-thread/15 text-thread",
  blocked: "bg-danger/15 text-danger",
  done: "bg-ok/15 text-ok",
  open: "bg-warn/15 text-warn",
  answered: "bg-ok/15 text-ok",
  escalated: "bg-danger/15 text-danger",
  resolved: "bg-ok/15 text-ok",
  active: "bg-thread/15 text-thread",
  proposed: "bg-raised text-ink-2",
  closing: "bg-warn/15 text-warn",
  closed: "bg-raised text-ink-2",
};

function Badge({ value }: { value: string }) {
  return (
    <span
      className={`inline-block whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ${
        STATUS_COLORS[value] ?? "bg-raised text-ink-2"
      }`}
    >
      {value.replace("_", " ")}
    </span>
  );
}

function Section({
  title,
  rows,
  render,
  empty,
}: {
  title: string;
  rows: Row[];
  render: (r: Row) => React.ReactNode;
  empty: string;
}) {
  return (
    <section className="rounded-xl border border-line bg-card p-4 shadow-card">
      <h2 className="mb-3 font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-ink-3">
        {title}
      </h2>
      {rows.length === 0 ? (
        <p className="text-sm text-ink-3">{empty}</p>
      ) : (
        <ul className="space-y-2">{rows.map((r) => render(r))}</ul>
      )}
    </section>
  );
}

/** The paid-for lessons, with a filter by the class of work that produced
 *  them.
 *
 *  `list_lessons` and its `project_class` filter shipped with the retro loop
 *  and the Season band has counted lessons ever since — but nothing listed
 *  one, so the count led nowhere and search rendered a lesson hit as dead
 *  text. A lesson the team cannot re-read is one it pays for twice.
 *
 *  Filters on the SERVER, not over the rows already fetched: the list is
 *  capped, and a client-side filter would quietly search only the newest
 *  page while looking like it searched everything. */
function LessonsCard() {
  // rows carry the filter they belong to. A plain list plus a synchronous
  // clear at the top of the effect is the same idea, but setState directly
  // inside an effect is what react-hooks/set-state-in-effect forbids — and
  // this shape also survives an out-of-order response without a second guard.
  const [rows, setRows] = useState<{ cls: string; list: Row[] } | null>(null);
  const [cls, setCls] = useState("");
  const [error, setError] = useState("");
  // classes come from the rows themselves — playbooks.py owns the real list
  // and a hardcoded copy here would drift the first time one is added
  const [classes, setClasses] = useState<string[]>([]);

  useEffect(() => {
    // `live` is the point: switch the filter twice and the FIRST response can
    // land last, leaving one class's rows under another class's label with no
    // way for the reader to tell. Clearing rows first means the card says
    // "loading" instead of showing the previous filter's rows for the whole
    // in-flight window.
    let live = true;
    const q = cls ? `?project_class=${encodeURIComponent(cls)}` : "";
    api<Row[]>(`/api/lessons${q}`)
      .then((r) => {
        if (!live) return;
        setRows({ cls, list: r });
        setError("");
        // only from the unfiltered read: a filtered one knows about one class
        if (!cls)
          setClasses([
            ...new Set(r.map((l) => String(l.project_class || "")).filter(Boolean)),
          ].sort());
      })
      .catch((e) => {
        if (!live) return;
        setRows({ cls, list: [] });
        setError(loadError(e));
      });
    return () => {
      live = false;
    };
  }, [cls]);
  // showing the PREVIOUS filter's rows under the new label is the bug this
  // closes; a mismatch is the loading state
  const list = rows && rows.cls === cls ? rows.list : null;

  return (
    <section className="rounded-xl border border-line bg-card p-4 shadow-card">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-ink-3">
          Lessons
        </h2>
        {classes.length > 0 ? (
          <select
            aria-label="Filter lessons by type of work"
            value={cls}
            onChange={(e) => setCls(e.target.value)}
            className="rounded-lg border border-line-strong bg-card px-2 py-1 text-xs outline-none"
          >
            <option value="">All</option>
            {classes.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        ) : null}
      </div>
      {error ? (
        <p className="text-sm text-danger">{error}</p>
      ) : list === null ? (
        <p className="text-sm text-ink-3">Loading…</p>
      ) : list.length === 0 ? (
        <p className="text-sm text-ink-3">
          {cls
            ? `No lesson recorded from ${cls} work yet.`
            : "No lesson recorded yet. Close an engagement to write the first one."}
        </p>
      ) : (
        <ul className="space-y-2">
          {list.map((l) => (
            <li key={l.id} id={`lesson-${l.id}`} className="text-sm">
              <span className="text-ink-3">#{l.id}</span> {l.lesson}
              {l.recommendation ? (
                <span className="block text-xs text-ink-3">
                  → {l.recommendation}
                </span>
              ) : null}
              <span className="block text-xs text-ink-3">
                {l.project_class ? `${l.project_class} · ` : ""}
                {l.created_by}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function StandupCard({ rows }: { rows: Row[] }) {
  return (
    <section className="rounded-xl border border-line bg-card p-4 shadow-card">
      <h2 className="mb-3 font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-ink-3">
        Recent standups
      </h2>
      {rows.length === 0 ? (
        <p className="text-sm text-ink-3">
          No standups posted yet — post yours from My Day.
        </p>
      ) : (
        <ul className="space-y-2">
          {rows.map((s) => (
            <li key={s.id} className="text-sm">
              <span className="font-medium">{s.author}</span>
              <VisibilityBadge
                visibility={s.visibility as string}
                crewId={s.crew_id as number}
              />
              <p className="text-xs text-ink-3">
                {s.today}
                {s.blockers ? (
                  <>
                    {" · "}
                    <span className="text-danger">blocked: {s.blockers}</span>
                  </>
                ) : null}
              </p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function EditRow({
  fields,
  onSave,
  onCancel,
}: {
  fields: Record<string, string>;
  onSave: (f: Record<string, string>) => void;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState(fields);
  return (
    <li
      className="flex flex-wrap items-center gap-1.5 text-sm"
      onKeyDown={(e) => e.key === "Escape" && onCancel()}
    >
      {Object.keys(fields).map((k, i) =>
        k === "content" ? (
          <textarea
            key={k}
            aria-label="Note content (markdown)"
            rows={4}
            value={draft[k]}
            onChange={(e) => setDraft({ ...draft, [k]: e.target.value })}
            className="w-full rounded-lg border border-line-strong bg-transparent px-2 py-1 text-xs outline-none focus:border-thread-solid"
          />
        ) : k === "assignee" ? (
          <PersonInput
            key={k}
            aria-label="Assignee"
            value={draft[k]}
            onChange={(e) => setDraft({ ...draft, [k]: e.target.value })}
            placeholder="assignee"
            className="w-28 rounded-lg border border-line-strong bg-transparent px-2 py-0.5 text-xs outline-none focus:border-thread-solid"
          />
        ) : (
          <input
            key={k}
            autoFocus={i === 0}
            aria-label={k.replace("_", " ")}
            type={k === "due_date" ? "date" : "text"}
            value={draft[k]}
            onChange={(e) => setDraft({ ...draft, [k]: e.target.value })}
            placeholder={k.replace("_", " ")}
            className={
              (k === "title" ? "min-w-40 flex-1" : "w-32") +
              " rounded-lg border border-line-strong bg-transparent px-2 py-0.5 text-xs outline-none focus:border-thread-solid"
            }
          />
        ),
      )}
      <button
        disabled={!draft.title?.trim()}
        onClick={() => {
          // clearing IS a correction: the service treats "" as "no change",
          // so a field the user emptied must travel as the "-" sentinel
          const out = { ...draft };
          for (const k of ["due_date", "assignee", "description"]) {
            if (k in out && !out[k] && fields[k]) out[k] = "-";
          }
          onSave(out);
        }}
        className="rounded bg-thread-solid px-2 py-0.5 text-xs font-medium text-white hover:opacity-90 disabled:opacity-40"
      >
        save
      </button>
      <button onClick={onCancel} className="text-xs text-ink-3 hover:text-ink">
        cancel
      </button>
    </li>
  );
}

/** The draft lives here (EditRow idiom): keystrokes re-render this form,
 *  not the thirteen-section page around it. */
function AbsenceForm({
  onAdd,
}: {
  onAdd: (draft: Record<string, string>) => Promise<void>;
}) {
  const empty = { person: "", starts_on: "", ends_on: "", kind: "pto" };
  const [draft, setDraft] = useState(empty);
  const [adding, setAdding] = useState(false);
  const add = async () => {
    setAdding(true);
    try {
      await onAdd(draft);
      setDraft(empty);
    } catch (e) {
      reportStatus(actionError(e));
    } finally {
      setAdding(false);
    }
  };
  return (
    <div className="mb-3 flex flex-wrap items-end gap-1.5 text-xs">
      <PersonInput
        aria-label="Who is away"
        name="person"
        value={draft.person}
        onChange={(e) => setDraft({ ...draft, person: e.target.value })}
        placeholder="who"
        className="w-28 rounded-lg border border-line-strong bg-transparent px-2 py-1 outline-none focus:border-thread-solid"
      />
      {/* visible captions: once the row wraps, two bare date inputs are
          indistinguishable — aria-labels don't help a sighted phone user */}
      <label className="flex flex-col gap-0.5">
        <span className="text-[10px] uppercase tracking-wide text-ink-3">
          from
        </span>
        <input
          type="date"
          aria-label="Away from"
          name="starts_on"
          value={draft.starts_on}
          onChange={(e) => setDraft({ ...draft, starts_on: e.target.value })}
          className="rounded-lg border border-line-strong bg-transparent px-2 py-1 outline-none focus:border-thread-solid"
        />
      </label>
      <label className="flex flex-col gap-0.5">
        <span className="text-[10px] uppercase tracking-wide text-ink-3">
          until
        </span>
        <input
          type="date"
          aria-label="Away until"
          name="ends_on"
          min={draft.starts_on || undefined}
          value={draft.ends_on}
          onChange={(e) => setDraft({ ...draft, ends_on: e.target.value })}
          className="rounded-lg border border-line-strong bg-transparent px-2 py-1 outline-none focus:border-thread-solid"
        />
      </label>
      <select
        aria-label="Kind of absence"
        name="kind"
        value={draft.kind}
        onChange={(e) => setDraft({ ...draft, kind: e.target.value })}
        className="rounded-lg border border-line-strong bg-card px-1.5 py-1"
      >
        <option value="pto">PTO</option>
        <option value="oncall">on-call</option>
        <option value="focus">focus</option>
      </select>
      <button
        disabled={
          adding ||
          !draft.person.trim() ||
          !draft.starts_on ||
          !draft.ends_on ||
          draft.ends_on < draft.starts_on
        }
        onClick={add}
        className="rounded-lg bg-thread-solid px-2.5 py-1 font-medium text-white hover:opacity-90 disabled:opacity-40"
      >
        {adding ? "Adding…" : "Add"}
      </button>
    </div>
  );
}

const COLLECTIONS = [
  "milestones",
  "tasks",
  "questions",
  "decisions",
  "standups",
  "events",
  "notes",
  "activity",
  "blockers",
  "engagements",
  "capacity",
  "absences",
];

function fetchCollection(name: string): Promise<Row[]> {
  if (name !== "events") return api<Row[]>(`/api/${name}`);
  // calendar shows what's ahead — without the cutoff the card fills with
  // the 50 oldest events and never today's
  const now = new Date();
  // cutoff = the EARLIER of local and UTC day: event timestamps are naive
  // UTC by convention but typed as local wall times in practice, and the
  // cutoff must never hide the rest of "today" on either side of UTC —
  // worst case it shows one extra stale day
  const localDay = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  const utcDay = now.toISOString().slice(0, 10);
  const today = localDay < utcDay ? localDay : utcDay;
  return api<Row[]>(`/api/events?from_date=${today}`);
}

const CONCLUSIONS = [
  "achieved",
  "partial",
  "missed",
  "invalidated",
  "unmeasured",
  "stopped",
] as const;

type PlanDiff = {
  playbook: string;
  drafts_lesson: boolean;
  slipped: { title: string; days: number; to: string; basis: string }[];
  unfinished_tasks: string[];
  dropped_tasks: string[];
  added_tasks: string[];
  skipped_rituals: string[];
};

/** The variance in one line. Counts only — the drafted lesson carries the
 *  detail, and a reviewer reads it there rather than inside a close button. */
function planDiffSummary(d: PlanDiff): string {
  const parts: string[] = [];
  const late = d.slipped.filter((s) => s.days > 0);
  if (late.length) {
    const worst = late.reduce((a, b) => (b.days > a.days ? b : a));
    // the same two bases the backend computes: a milestone that LANDED late
    // and one that was re-dated are different facts about the plan
    const how = worst.basis === "finished" ? "landed late" : "moved";
    parts.push(
      `${late.length} milestone${late.length === 1 ? "" : "s"} ${how}, the largest by ${worst.days} day${worst.days === 1 ? "" : "s"}`,
    );
  }
  const unfinished = d.unfinished_tasks.length + d.dropped_tasks.length;
  if (unfinished)
    parts.push(
      `${unfinished} planned task${unfinished === 1 ? "" : "s"} never finished`,
    );
  if (d.added_tasks.length)
    parts.push(
      `${d.added_tasks.length} task${d.added_tasks.length === 1 ? "" : "s"} added`,
    );
  if (d.skipped_rituals.length)
    parts.push(
      `${d.skipped_rituals.length} ritual${d.skipped_rituals.length === 1 ? "" : "s"} did not happen`,
    );
  return parts.length ? `${parts.join(", ")}.` : "it went to plan.";
}

const CONCLUSION_HINTS: Record<string, string> = {
  achieved: "the outcome landed",
  partial: "some of it landed",
  missed: "the outcome did not land",
  invalidated: "the experiment disproved the idea — on time, that is a win",
  unmeasured: "closed without measuring the outcome",
  stopped: "halted early on purpose",
};

const SHIPPED_WINDOW_DAYS = 7;

/** Done tasks from the last week, newest first. A module function, not an
 * expression in the component body: the clock read belongs outside render,
 * the way lib/time.ts::timeAgo holds its own. The rows are already in the
 * Tasks payload and thrown away by that section's filter, so this costs no
 * request. completed_at is a UTC timestamp, so the window compares instants
 * rather than slicing a date out of a string. */
function shippedRecently(tasks: Row[] | undefined): Row[] {
  const cutoff = Date.now() - SHIPPED_WINDOW_DAYS * 86_400_000;
  return (tasks ?? [])
    .filter((t) => {
      if (t.status !== "done" || !t.completed_at) return false;
      const at = Date.parse(String(t.completed_at));
      return Number.isFinite(at) && at >= cutoff;
    })
    .sort((a, b) => String(b.completed_at).localeCompare(String(a.completed_at)));
}

export default function Dashboard() {
  const [data, setData] = useState<Record<string, Row[]>>({});
  const [pulse, setPulse] = useState<Pulse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [closing, setClosing] = useState<number | null>(null);
  // What the playbook said, against what happened. Fetched when the control
  // opens rather than with the engagement list: it is one read per close, and
  // every engagement carrying its own diff would be N reads nobody looks at.
  const [planDiff, setPlanDiff] = useState<PlanDiff | null>(null);
  // which engagement the open panel belongs to, readable synchronously by the
  // in-flight plan-diff fetch below
  const closingRef = useRef<number | null>(null);
  const [draftedLesson, setDraftedLesson] = useState<number | null>(null);
  const [assigning, setAssigning] = useState<number | null>(null);
  const [answering, setAnswering] = useState<number | null>(null);
  const [editing, setEditing] = useState<{
    kind: "task" | "milestone";
    id: number;
  } | null>(null);
  const [editingNote, setEditingNote] = useState<number | null>(null);
  const [deletingNote, setDeletingNote] = useState<number | null>(null);
  const [deletingAbsence, setDeletingAbsence] = useState<number | null>(null);

  // inline actions re-fetch instead of window.location.reload() — a reload
  // resets focus to the document top and strips a screen-reader user of all
  // context mid-task
  // last-request-wins PER COLLECTION: two quick mutations can refresh
  // disjoint subsets concurrently, so an older result only loses to a newer
  // refresh that re-requested the SAME collection — discarding the whole
  // older snapshot would leave its collections stale with nothing left in
  // flight to refetch them (the user's own edit would look like a lost save)
  const generation = useRef(0);
  const collectionGen = useRef<Record<string, number>>({});
  // collections whose LAST refresh failed — the error banner stays up until
  // every one of them is redelivered, so a success over other collections
  // cannot mask a failure that left these stale
  const failedNames = useRef<Set<string>>(new Set());
  // an inline mutation refreshes ONLY the collections it changed (the write
  // endpoints answer with summaries, not rows, so local patching is not an
  // option) — the full 13-endpoint sweep is for mount and retry. Every
  // handler adds "activity" (every service write logs there) and pulse
  // rides along (blocker/engagement/standup writes move its numbers).
  const refresh = useCallback((names: string[]) => {
    const g = ++generation.current;
    for (const e of names) collectionGen.current[e] = g;
    Promise.all(names.map(async (e) => [e, await fetchCollection(e)] as const))
      .then((pairs) => {
        const fresh = pairs.filter(([e]) => collectionGen.current[e] === g);
        if (fresh.length === 0) return;
        setData((prev) => ({ ...prev, ...Object.fromEntries(fresh) }));
        for (const [e] of fresh) failedNames.current.delete(e);
        if (failedNames.current.size === 0) setError(null);
      })
      .catch((err) => {
        // scope the failure to collections still claimed by THIS refresh:
        // ones a newer refresh re-claimed are that refresh's to deliver or
        // fail, so this failure is obsolete for them. Stale claims are
        // harmless to leave — each resolution compares against its own g,
        // and generations are never reused.
        const mine = names.filter((e) => collectionGen.current[e] === g);
        if (mine.length > 0) {
          for (const e of mine) failedNames.current.add(e);
          setError(loadError(err));
        }
      });
    api<Pulse>("/api/pulse")
      .then((p) => {
        if (g === generation.current) setPulse(p);
      })
      .catch(() => {}); // pulse is decorative — its failure must not blank the page
  }, []);
  const load = useCallback(() => refresh(COLLECTIONS), [refresh]);
  useEffect(load, [load]);

  const refocusEdit = (kind: string, id: number) =>
    setTimeout(() => document.getElementById(`edit-${kind}-${id}`)?.focus(), 0);

  const addAbsence = async (draft: Record<string, string>) => {
    // absences feed capacity's "away" markers — both must refresh together
    await api("/api/absences", { method: "POST", body: JSON.stringify(draft) });
    refresh(["absences", "capacity", "activity"]);
  };

  const deleteAbsence = async (id: number) => {
    try {
      await api(`/api/absences/${id}`, { method: "DELETE" });
      setDeletingAbsence(null);
      refresh(["absences", "capacity", "activity"]);
    } catch (e) {
      reportStatus(actionError(e));
    }
  };

  const patchRow = async (
    entity: "tasks" | "milestones",
    id: number,
    fields: Record<string, string>,
  ) => {
    try {
      await api(`/api/${entity}/${id}`, {
        method: "PATCH",
        body: JSON.stringify(fields),
      });
      setEditing(null);
      refocusEdit(entity === "tasks" ? "task" : "milestone", id);
      refresh([entity, "activity"]);
    } catch (e) {
      reportStatus(actionError(e));
    }
  };

  const patchNote = async (id: number, f: Record<string, string>) => {
    try {
      await api(`/api/notes/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ topic: f.title, content: f.content }),
      });
      setEditingNote(null);
      refocusEdit("note", id);
      refresh(["notes", "activity"]);
    } catch (e) {
      reportStatus(actionError(e));
    }
  };

  const deleteNote = async (id: number) => {
    try {
      await api(`/api/notes/${id}`, { method: "DELETE" });
      setDeletingNote(null);
      refresh(["notes", "activity"]);
    } catch (e) {
      reportStatus(actionError(e));
    }
  };

  const assignTo = async (qid: number, who: string) => {
    try {
      await api(`/api/questions/${qid}`, {
        method: "PATCH",
        body: JSON.stringify({ assigned_to: who }),
      });
      setAssigning(null);
      refresh(["questions", "activity"]);
    } catch (e) {
      reportStatus(actionError(e));
    }
  };

  const recentlyShipped = shippedRecently(data.tasks);

  // full-page error only before the first successful load — after that a
  // failed refresh keeps the data on screen with a banner (My Day idiom)
  if (error && Object.keys(data).length === 0) {
    return (
      <main
        id="content"
        tabIndex={-1}
        className="mx-auto w-full max-w-5xl p-4 sm:p-6 xl:max-w-6xl"
      >
        <SectionTabs set="work" />
        <p className="text-sm text-danger">
          {error}
          <button onClick={load} className="ml-2 underline">
            retry
          </button>
        </p>
      </main>
    );
  }

  if (Object.keys(data).length === 0)
    return (
      <main
        id="content"
        tabIndex={-1}
        className="mx-auto w-full max-w-5xl xl:max-w-6xl p-4 sm:p-6"
      >
        <SectionTabs set="work" />
        <h1 className="mb-1 font-display text-[24px]/[1.15] font-semibold tracking-[-0.01em] text-ink">
          Browse
        </h1>
        <p className="mb-6 max-w-3xl text-sm text-ink-3">{loadingLine()}</p>
      </main>
    );

  return (
    <main
      id="content"
      tabIndex={-1}
      className="mx-auto w-full max-w-5xl xl:max-w-6xl p-4 sm:p-6"
    >
      <SectionTabs set="work" />
      <h1 className="mb-1 font-display text-[24px]/[1.15] font-semibold tracking-[-0.01em] text-ink">
        Browse
      </h1>
      <p className="mb-6 max-w-3xl text-sm text-ink-3">
        Everything the team tracks — edit inline wherever you see it.
      </p>
      {error && (
        <p
          role="alert"
          className="mb-4 rounded-lg border border-danger/40 bg-danger/10 px-3 py-2 text-xs text-danger"
        >
          Refresh failed ({error}) — showing the previous state.
          <button onClick={load} className="ml-2 underline">
            retry
          </button>
        </p>
      )}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {pulse && (
          <section className="rounded-xl border border-line bg-card p-4 shadow-card md:col-span-2 loom-band">
            <h2 className="mb-3 font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-thread">
              Season {pulse.season.label}
              {/* a real space, not just the margin: CSS gaps separate pixels,
                not text, so "S5" + "0 days left" was read as "S50 days left" */}{" "}
              <span className="ml-2 font-normal normal-case text-ink-3">
                {pulse.season.days_left} days left
              </span>
            </h2>
            <div className="grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
              <div>
                <p className="font-display text-[30px]/none font-semibold text-ink">
                  {pulse.standup_chain.chain}{" "}
                  {/* the unit sits INLINE with the number, so it reads as a
                    phrase and must agree — the (s) allowance covers standalone
                    stat labels, not "1 days" set in 30px type */}
                  <span className="ml-1 text-sm font-normal text-ink-3">
                    {pulse.standup_chain.chain === 1 ? "day" : "days"}
                  </span>
                </p>
                <span
                  aria-hidden
                  className={`my-1.5 block h-0.5 w-6 rounded-full ${pulse.standup_chain.chain > 0 ? "bg-ok" : "bg-line-strong"}`}
                />
                <p className="text-xs text-ink-3">standup chain (whole team)</p>
              </div>
              <div>
                <p className="font-display text-[30px]/none font-semibold text-ink">
                  {pulse.season_totals.engagements_shipped}
                </p>
                <span
                  aria-hidden
                  className={`my-1.5 block h-0.5 w-6 rounded-full ${pulse.season_totals.engagements_shipped > 0 ? "bg-weld" : "bg-line-strong"}`}
                />
                <p className="text-xs text-ink-3">shipped this season</p>
              </div>
              <div>
                <p className="font-display text-[30px]/none font-semibold text-ink">
                  {pulse.season_totals.blockers_spotted}{" "}
                  <span className="ml-1 text-sm font-normal text-ink-3">
                    / {pulse.season_totals.blockers_open} open
                  </span>
                </p>
                <span
                  aria-hidden
                  className={`my-1.5 block h-0.5 w-6 rounded-full ${pulse.season_totals.blockers_open > 0 ? "bg-danger" : "bg-line-strong"}`}
                />
                <p className="text-xs text-ink-3">
                  blockers spotted — spotting one is a win
                </p>
              </div>
              <div>
                <p className="font-display text-[30px]/none font-semibold text-ink">
                  {pulse.season_totals.lessons_recorded}
                </p>
                <span
                  aria-hidden
                  className={`my-1.5 block h-0.5 w-6 rounded-full ${pulse.season_totals.lessons_recorded > 0 ? "bg-thread-solid" : "bg-line-strong"}`}
                />
                <p className="text-xs text-ink-3">lessons recorded</p>
              </div>
            </div>
            {pulse.blocker_speedrun.length > 0 && (
              <p className="mt-3 text-xs text-ink-3">
                ⏱️ Time to clear blockers this season, by impact:{" "}
                {pulse.blocker_speedrun
                  .map(
                    (s) =>
                      `${s.impact} — avg ${s.avg_hours}h (fastest ${s.best_hours}h)`,
                  )
                  .join(" · ")}
              </p>
            )}
          </section>
        )}
        {draftedLesson ? (
          <p className="rounded border border-line bg-raised px-3 py-2 text-xs text-ink-2">
            A close-out lesson is drafted from the plan variance.{" "}
            <Link href="/review" className="underline hover:text-ink">
              Approve or reject it on Review
            </Link>
            . An approved one reaches the next kickoff of this class. It names
            the playbook file for a human to edit, and edits nothing itself.{" "}
            <button
              onClick={() => setDraftedLesson(null)}
              className="text-ink-3 underline hover:text-ink"
            >
              dismiss
            </button>
          </p>
        ) : null}
        <Section
          title="Engagements"
          rows={data.engagements ?? []}
          empty="No engagements — accept a request (Inbox → Requests) or start one from a playbook."
          render={(e) => (
            <li
              key={e.id}
              className="flex flex-wrap items-start justify-between gap-3 text-sm"
            >
              <span className="min-w-0">
                <span className="flex items-center gap-2">
                  <span className="font-mono text-xs text-ink-3">#{e.id}</span>
                  <span className="truncate font-medium">{e.name}</span>
                  <VisibilityBadge
                    visibility={e.visibility as string}
                    crewId={e.crew_id as number}
                  />
                  {e.kind === "experiment" && (
                    <span className="whitespace-nowrap rounded-full border border-weld/25 bg-weld/10 px-1.5 py-px font-mono text-[10px] text-weld">
                      experiment
                    </span>
                  )}
                </span>
                <span className="mt-0.5 block font-mono text-[11px] text-ink-3">
                  {e.project_class}
                  {e.lead ? ` · lead @${e.lead}` : ""}
                  {e.kind === "experiment" && e.timebox_end
                    ? ` · timebox ${String(e.timebox_end)}`
                    : ""}
                  {e.conclusion ? ` · ${String(e.conclusion)}` : ""}
                </span>
              </span>
              <span className="flex shrink-0 items-center gap-2">
                {e.status !== "closed" && closing !== e.id && (
                  <button
                    onClick={() => {
                      const want = Number(e.id);
                      closingRef.current = want;
                      setClosing(want);
                      setPlanDiff(null);
                      api<PlanDiff>(`/api/engagements/${e.id}/plan-diff`)
                        // the id guard is the point: open close-out on A, then
                        // on B before A resolves, and B's panel would show A's
                        // variance directly above the button that closes B
                        // a ref, not a setState updater: React requires
                        // updaters to be pure, and StrictMode double-invokes
                        // them. The catch needs the same guard, or a late
                        // failure for A clears B's diff.
                        .then((d) => {
                          if (closingRef.current === want)
                            setPlanDiff(d.playbook ? d : null);
                        })
                        .catch(() => {
                          if (closingRef.current === want) setPlanDiff(null);
                        });
                    }}
                    className="whitespace-nowrap rounded bg-raised px-2 py-0.5 text-xs text-ink-2 hover:bg-line"
                  >
                    close out…
                  </button>
                )}
                <Badge value={String(e.status)} />
              </span>
              {closing === e.id && (
                <span
                  className="mt-1.5 flex w-full flex-wrap items-center gap-1.5 text-xs"
                  onKeyDown={(ev) => {
                    if (ev.key !== "Escape") return;
                    closingRef.current = null;
                    setClosing(null);
                  }}
                >
                  {planDiff ? (
                    <span className="w-full rounded bg-raised px-2 py-1.5 text-[11px] text-ink-2">
                      <span className="font-medium">
                        Against the {planDiff.playbook} playbook:
                      </span>{" "}
                      {planDiffSummary(planDiff)}
                      <span className="mt-0.5 block text-ink-3">
                        {planDiff.drafts_lesson
                          ? "Closing drafts a lesson from this, for somebody to approve on Review. Editing the playbook file stays a human job."
                          : "This engagement is not workspace-wide, so closing drafts no lesson."}
                      </span>
                    </span>
                  ) : null}
                  <span className="text-ink-3">How did it end?</span>
                  <span className="w-full text-[11px] text-ink-3">
                    invalidated = disproved on time (a win) · unmeasured =
                    closed without measuring
                  </span>
                  {CONCLUSIONS.map((c, ci) => (
                    <button
                      key={c}
                      autoFocus={ci === 0}
                      onClick={async () => {
                        try {
                          const out = await api<{
                            lesson_proposal_id?: number;
                          }>(`/api/engagements/${e.id}`, {
                            method: "PATCH",
                            body: JSON.stringify({
                              status: "closed",
                              conclusion: c,
                            }),
                          });
                          setDraftedLesson(
                            typeof out?.lesson_proposal_id === "number" &&
                              out.lesson_proposal_id > 0
                              ? out.lesson_proposal_id
                              : null,
                          );
                          closingRef.current = null;
                          setClosing(null);
                          setPlanDiff(null);
                          // closing removes the engagement's allocations from
                          // capacity and ships a recap note — both render here
                          refresh([
                            "engagements",
                            "capacity",
                            "notes",
                            "activity",
                          ]);
                        } catch (err) {
                          reportStatus(actionError(err));
                        }
                      }}
                      title={CONCLUSION_HINTS[c]}
                      className="rounded bg-raised px-2 py-0.5 hover:bg-line"
                    >
                      {c}
                    </button>
                  ))}
                  <button
                    onClick={() => {
                      closingRef.current = null;
                      setClosing(null);
                    }}
                    className="text-ink-3 hover:text-ink"
                  >
                    cancel
                  </button>
                </span>
              )}
            </li>
          )}
        />
        <Section
          title="Blockers"
          rows={data.blockers ?? []}
          empty={emptyState("blockers")}
          render={(b) => (
            <li
              key={b.id}
              className="flex items-center justify-between gap-2 text-sm"
            >
              <span>
                <span className="text-ink-3">#{b.id}</span> {b.title}
                <VisibilityBadge
                  visibility={b.visibility as string}
                  crewId={b.crew_id as number}
                />
                <span className="ml-2 text-xs text-ink-3">
                  {b.owner ? `@${b.owner}` : "unowned"} · {b.impact}
                </span>
              </span>
              <Badge value={String(b.status)} />
            </li>
          )}
        />
        <Section
          title="Capacity"
          rows={data.capacity ?? []}
          empty="No allocations recorded."
          render={(c) => (
            <li
              key={String(c.person)}
              className="flex items-center justify-between text-sm"
            >
              <span>
                {c.person}
                {c.away ? (
                  <span className="ml-1.5 rounded-full bg-weld/15 px-1.5 py-px font-mono text-[10px] text-weld">
                    away · {c.away}
                  </span>
                ) : null}
                <span className="ml-2 text-xs text-ink-3">{c.detail}</span>
              </span>
              <span
                className={`text-xs font-semibold ${
                  Number(c.total_percent) > 100 ? "text-danger" : "text-ok"
                }`}
              >
                {c.total_percent}%
              </span>
            </li>
          )}
        />
        <section className="rounded-xl border border-line bg-card p-4 shadow-card">
          <h2 className="mb-3 font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-ink-3">
            Time away
          </h2>
          <p className="mb-2 text-xs text-ink-3">
            PTO zeroes someone out of capacity and the weekly plan. On-call and
            focus are advisory context for staffing calls.
          </p>
          <AbsenceForm onAdd={addAbsence} />
          {(data.absences ?? []).length === 0 ? (
            <p className="text-sm text-ink-3">Nobody is scheduled away.</p>
          ) : (
            <ul className="space-y-1 text-sm">
              {(data.absences ?? []).map((a) => (
                <li
                  key={a.id}
                  className="flex items-center justify-between gap-2"
                >
                  <span>
                    {a.person}
                    <VisibilityBadge
                      visibility={a.visibility as string}
                      crewId={a.crew_id as number}
                    />
                    <span className="ml-2 text-xs text-ink-3">
                      {a.kind} · {a.starts_on} → {a.ends_on}
                      {a.note ? ` · ${a.note}` : ""}
                    </span>
                  </span>
                  {deletingAbsence === a.id ? (
                    <span className="flex shrink-0 gap-3 md:gap-1.5">
                      <button
                        autoFocus
                        aria-label={`Delete ${a.person}'s ${a.kind} ${a.starts_on} for good`}
                        onClick={() => deleteAbsence(Number(a.id))}
                        className="rounded bg-danger-solid px-2 py-1.5 md:py-0.5 text-xs font-medium text-white hover:opacity-90"
                      >
                        delete for good
                      </button>
                      <button
                        onClick={() => setDeletingAbsence(null)}
                        className="rounded px-2 py-0.5 text-xs text-ink-3 hover:text-ink"
                      >
                        keep
                      </button>
                    </span>
                  ) : (
                    <button
                      aria-label={`Delete ${a.person}'s ${a.kind} ${a.starts_on}`}
                      onClick={() => setDeletingAbsence(Number(a.id))}
                      className="shrink-0 rounded bg-raised px-2 py-0.5 text-xs text-danger hover:bg-line"
                    >
                      delete…
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>
        <Section
          title="Milestones"
          rows={data.milestones ?? []}
          empty="No milestones yet — ask the Chief of Staff in Chat to plan a project."
          render={(m) =>
            editing?.kind === "milestone" && editing.id === m.id ? (
              <EditRow
                key={m.id}
                fields={{
                  title: String(m.title),
                  due_date: String(m.due_date ?? ""),
                }}
                onSave={(f) => patchRow("milestones", Number(m.id), f)}
                onCancel={() => {
                  setEditing(null);
                  refocusEdit("milestone", Number(m.id));
                }}
              />
            ) : (
              <li
                key={m.id}
                className="flex items-center justify-between gap-2 text-sm"
              >
                <span>
                  <span className="text-ink-3">#{m.id}</span> {m.title}
                  <VisibilityBadge
                    visibility={m.visibility as string}
                    crewId={m.crew_id as number}
                  />
                  {m.due_date ? (
                    <span className="ml-2 text-xs text-ink-3">
                      due {m.due_date}
                    </span>
                  ) : null}
                </span>
                <span className="flex items-center gap-1">
                  <button
                    id={`edit-milestone-${m.id}`}
                    aria-label={`Edit milestone #${m.id}: ${m.title}`}
                    onClick={() =>
                      setEditing({ kind: "milestone", id: Number(m.id) })
                    }
                    className="rounded bg-raised px-2 py-0.5 text-xs text-ink-2 hover:bg-line"
                  >
                    edit…
                  </button>
                  <Badge value={String(m.status)} />
                </span>
              </li>
            )
          }
        />
        <Section
          title="Tasks"
          rows={(data.tasks ?? []).filter((t) => t.status !== "done")}
          empty="No open tasks — open quick capture and type 'todo: …'."
          render={(t) =>
            editing?.kind === "task" && editing.id === t.id ? (
              <EditRow
                key={t.id}
                fields={{
                  title: String(t.title),
                  assignee: String(t.assignee ?? ""),
                  due_date: String(t.due_date ?? ""),
                }}
                onSave={(f) => patchRow("tasks", Number(t.id), f)}
                onCancel={() => {
                  setEditing(null);
                  refocusEdit("task", Number(t.id));
                }}
              />
            ) : (
              <li
                key={t.id}
                className="flex items-center justify-between gap-2 text-sm"
              >
                <span className="min-w-0 break-words">
                  <PeekLink taskId={Number(t.id)}>
                    <span className="text-ink-3">#{t.id}</span> {t.title}
                  </PeekLink>
                  <VisibilityBadge
                    visibility={t.visibility as string}
                    crewId={t.crew_id as number}
                  />
                  {t.assignee ? (
                    <span className="ml-2 text-xs text-ink-3">
                      @{t.assignee}
                    </span>
                  ) : null}
                  {t.forge_url ? (
                    // the name repeats on every row, so a screen reader's link
                    // list reads "code, code, code" without the label. Safe as
                    // a bare href because services/forge.py::_clean_url is the
                    // only writer and admits bounded http(s) only.
                    <a
                      href={String(t.forge_url)}
                      target="_blank"
                      rel="noopener noreferrer"
                      aria-label={`Code for task #${t.id}: ${t.title} (opens a new tab)`}
                      className="ml-2 text-xs text-ink-3 underline hover:text-ink-2"
                    >
                      code <span aria-hidden>↗</span>
                    </a>
                  ) : null}
                </span>
                <span className="flex shrink-0 items-center gap-1">
                  <button
                    id={`edit-task-${t.id}`}
                    aria-label={`Edit task #${t.id}: ${t.title}`}
                    onClick={() =>
                      setEditing({ kind: "task", id: Number(t.id) })
                    }
                    className="rounded bg-raised px-2 py-0.5 text-xs text-ink-2 hover:bg-line"
                  >
                    edit…
                  </button>
                  <Badge value={String(t.priority)} />
                  <Badge value={String(t.status)} />
                </span>
              </li>
            )
          }
        />
        <Section
          title="Recently shipped"
          // The Tasks section above hides done work, so a merge that closes a
          // task removes it and its forge link in the same second — the one
          // moment worth showing had no surface. Independent of the
          // commitment line ON PURPOSE: Health's week plan lists done work
          // only when it was committed to a week, which is most of it missing.
          rows={recentlyShipped}
          empty={`Nothing shipped in the last ${SHIPPED_WINDOW_DAYS} days.`}
          render={(t) => (
            <li
              key={t.id}
              className="flex items-center justify-between gap-2 text-sm"
            >
              <span className="min-w-0 break-words">
                <PeekLink taskId={Number(t.id)}>
                  <span className="text-ink-3">#{t.id}</span> {t.title}
                </PeekLink>
                <VisibilityBadge
                  visibility={t.visibility as string}
                  crewId={t.crew_id as number}
                />
                {t.forge_url ? (
                  // same bare-href reasoning as the Tasks section above
                  <a
                    href={String(t.forge_url)}
                    target="_blank"
                    rel="noopener noreferrer"
                    aria-label={`Code for task #${t.id}: ${t.title} (opens a new tab)`}
                    className="ml-2 text-xs text-ink-3 underline hover:text-ink-2"
                  >
                    code <span aria-hidden>↗</span>
                  </a>
                ) : null}
              </span>
              <span className="shrink-0 text-xs text-ink-3">
                {String(t.completed_at ?? "").slice(0, 10)}
              </span>
            </li>
          )}
        />
        <Section
          title="Open questions"
          rows={data.questions ?? []}
          empty="No questions logged."
          render={(q) => (
            <li key={q.id} className="text-sm">
              <div className="flex items-center justify-between gap-2">
                <span>
                  <span className="text-ink-3">#{q.id}</span> {q.question}
                  <VisibilityBadge
                    visibility={q.visibility as string}
                    crewId={q.crew_id as number}
                  />
                </span>
                <Badge value={String(q.status)} />
              </div>
              {q.status === "open" && (
                <p className="mt-0.5 text-xs text-ink-3">
                  {assigning === q.id ? (
                    <span className="flex items-center gap-1.5">
                      <PersonInput
                        autoFocus
                        name="assign-question"
                        aria-label="Assign this question to"
                        placeholder="teammate's name — Enter to assign"
                        onKeyDown={(ev) => {
                          if (ev.key === "Escape") setAssigning(null);
                          const who = (
                            ev.target as HTMLInputElement
                          ).value.trim();
                          if (ev.key === "Enter" && who)
                            assignTo(Number(q.id), who);
                        }}
                        onChange={(ev) => {
                          // a mouse-picked datalist suggestion must commit too —
                          // picks arrive as insertReplacementText (or undefined
                          // inputType in Firefox), typing as insertText
                          const t = (ev.nativeEvent as InputEvent).inputType;
                          if (t && t !== "insertReplacementText") return;
                          const who = ev.target.value.trim();
                          if (who) assignTo(Number(q.id), who);
                        }}
                        className="rounded-lg border border-line-strong bg-transparent px-2 py-0.5 outline-none focus:border-thread-solid"
                      />
                      <button
                        onClick={() => setAssigning(null)}
                        className="hover:text-ink"
                      >
                        cancel
                      </button>
                    </span>
                  ) : answering === q.id ? (
                    <span className="flex items-center gap-1.5">
                      <input
                        autoFocus
                        name="answer-question"
                        aria-label="Answer this question"
                        placeholder="the answer — Enter to record it"
                        onKeyDown={async (ev) => {
                          if (ev.key === "Escape") setAnswering(null);
                          const answer = (
                            ev.target as HTMLInputElement
                          ).value.trim();
                          if (ev.key !== "Enter" || !answer) return;
                          try {
                            await api(`/api/questions/${q.id}/answer`, {
                              method: "POST",
                              body: JSON.stringify({ answer }),
                            });
                            setAnswering(null);
                            refresh(["questions", "activity"]);
                          } catch (e) {
                            reportStatus(actionError(e));
                          }
                        }}
                        className="w-64 rounded-lg border border-line-strong bg-transparent px-2 py-0.5 outline-none focus:border-thread-solid"
                      />
                      <button
                        onClick={() => setAnswering(null)}
                        className="hover:text-ink"
                      >
                        cancel
                      </button>
                    </span>
                  ) : (
                    <span className="flex items-center gap-2">
                      {q.assigned_to ? (
                        <span>→ @{q.assigned_to}</span>
                      ) : (
                        <button
                          onClick={() => setAssigning(Number(q.id))}
                          className="underline hover:text-ink-2"
                        >
                          unassigned — assign…
                        </button>
                      )}
                      <button
                        onClick={() => setAnswering(Number(q.id))}
                        className="underline hover:text-ink-2"
                      >
                        answer…
                      </button>
                    </span>
                  )}
                </p>
              )}
              {q.answer ? (
                <p className="mt-1 text-xs text-ink-3">↳ {q.answer}</p>
              ) : null}
            </li>
          )}
        />
        <Section
          title="Decisions"
          rows={data.decisions ?? []}
          empty="No decisions recorded."
          render={(d) => (
            <li key={d.id} className="text-sm">
              <span className="font-medium">{d.title}</span>
              <VisibilityBadge
                visibility={d.visibility as string}
                crewId={d.crew_id as number}
              />
              {d.decision !== d.title && (
                <p className="text-xs text-ink-3">{d.decision}</p>
              )}
            </li>
          )}
        />
        <StandupCard rows={data.standups ?? []} />
        <LessonsCard />
        <Section
          title="Calendar"
          rows={data.events ?? []}
          empty="Nothing scheduled — ask the Chief of Staff in Chat to schedule an event."
          render={(e) => (
            <li
              key={e.id}
              className="flex items-center justify-between text-sm"
            >
              <span>
                {e.title}
                <VisibilityBadge
                  visibility={e.visibility as string}
                  crewId={e.crew_id as number}
                />
              </span>
              <span className="text-xs text-ink-3">{e.starts_at}</span>
            </li>
          )}
        />
        <Section
          title="Knowledge base"
          rows={data.notes ?? []}
          empty="No notes saved."
          render={(n) =>
            editingNote === n.id ? (
              <EditRow
                key={n.id}
                fields={{ title: String(n.topic), content: String(n.content) }}
                onSave={(f) => patchNote(Number(n.id), f)}
                onCancel={() => {
                  setEditingNote(null);
                  refocusEdit("note", Number(n.id));
                }}
              />
            ) : (
              <li key={n.id} className="text-sm">
                <span className="flex items-center justify-between gap-2">
                  <span className="font-medium">
                    {n.topic}
                    <VisibilityBadge
                      visibility={n.visibility as string}
                      crewId={n.crew_id as number}
                    />
                  </span>
                  <span className="flex shrink-0 gap-1">
                    <button
                      id={`edit-note-${n.id}`}
                      aria-label={`Edit note: ${n.topic}`}
                      onClick={() => {
                        setDeletingNote(null);
                        setEditingNote(Number(n.id));
                      }}
                      className="rounded bg-raised px-2 py-0.5 text-xs text-ink-2 hover:bg-line"
                    >
                      edit…
                    </button>
                    {deletingNote === n.id ? (
                      <>
                        <button
                          autoFocus
                          aria-label={`Delete note ${n.topic} for good`}
                          onClick={() => deleteNote(Number(n.id))}
                          className="rounded bg-danger-solid px-2 py-1.5 md:py-0.5 text-xs font-medium text-white hover:opacity-90"
                        >
                          delete for good
                        </button>
                        <button
                          onClick={() => setDeletingNote(null)}
                          className="rounded px-2 py-0.5 text-xs text-ink-3 hover:text-ink"
                        >
                          keep
                        </button>
                      </>
                    ) : (
                      <button
                        aria-label={`Delete note: ${n.topic}`}
                        onClick={() => setDeletingNote(Number(n.id))}
                        className="rounded bg-raised px-2 py-0.5 text-xs text-danger hover:bg-line"
                      >
                        delete…
                      </button>
                    )}
                  </span>
                </span>
                <p className="line-clamp-2 text-xs text-ink-3">
                  {/* notes hold markdown; this is a plain-text preview */}
                  {String(n.content).replace(/[*#`]/g, "").replace(/\s+/g, " ")}
                </p>
              </li>
            )
          }
        />
        <Section
          title="Recent activity"
          rows={data.activity ?? []}
          empty="No activity yet."
          render={(a) => (
            <li key={a.id} className="text-xs text-ink-3">
              <span className="font-medium text-ink-2">{a.actor}</span>{" "}
              {String(a.action).replace("_", " ")} {a.detail}
              <time
                dateTime={String(a.created_at)}
                title={String(a.created_at)}
                className="ml-1 text-ink-3"
              >
                {timeAgo(String(a.created_at))}
              </time>
            </li>
          )}
        />
      </div>
    </main>
  );
}
