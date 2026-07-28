"use client";

import { useCallback, useEffect, useState } from "react";

import { API_URL, api } from "@/lib/api";
import { PersonInput } from "@/components/person-input";
import { SectionTabs } from "@/components/section-tabs";
import { timeAgo } from "@/lib/time";
import { emptyState, loadingLine } from "@/lib/whimsy";

type Pulse = {
  season: { label: string; days_left: number };
  standup_chain: { chain: number; humans: number };
  blocker_speedrun: { impact: string; cleared: number; avg_hours: number; best_hours: number }[];
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

const CONCLUSIONS = [
  "achieved",
  "partial",
  "missed",
  "invalidated",
  "unmeasured",
  "stopped",
] as const;

const CONCLUSION_HINTS: Record<string, string> = {
  achieved: "the outcome landed",
  partial: "some of it landed",
  missed: "the outcome didn't land",
  invalidated: "the experiment disproved the idea — on time, that's a win",
  unmeasured: "closed without measuring the outcome",
  stopped: "halted early on purpose",
};

export default function Dashboard() {
  const [data, setData] = useState<Record<string, Row[]>>({});
  const [pulse, setPulse] = useState<Pulse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [closing, setClosing] = useState<number | null>(null);
  const [assigning, setAssigning] = useState<number | null>(null);
  const [answering, setAnswering] = useState<number | null>(null);
  const [editing, setEditing] = useState<{ kind: "task" | "milestone"; id: number } | null>(
    null,
  );
  const [editingNote, setEditingNote] = useState<number | null>(null);
  const [deletingNote, setDeletingNote] = useState<number | null>(null);
  const [absDraft, setAbsDraft] = useState({
    person: "",
    starts_on: "",
    ends_on: "",
    kind: "pto",
  });


  // inline actions re-fetch instead of window.location.reload() — a reload
  // resets focus to the document top and strips a screen-reader user of all
  // context mid-task
  const load = useCallback(() => {
    const endpoints = [
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
    // calendar shows what's ahead — without the cutoff the card fills with
    // the 50 oldest events and never today's
    const today = new Date().toISOString().slice(0, 10);
    Promise.all(
      endpoints.map(
        async (e) =>
          [
            e,
            await api<Row[]>(
              e === "events" ? `/api/events?from_date=${today}` : `/api/${e}`,
            ),
          ] as const,
      ),
    )
      .then((pairs) => setData(Object.fromEntries(pairs)))
      .catch((err) => setError(String(err)));
    api<Pulse>("/api/pulse")
      .then(setPulse)
      .catch(() => {}); // pulse is decorative — its failure must not blank the page
  }, []);
  useEffect(load, [load]);

  const refocusEdit = (kind: string, id: number) =>
    setTimeout(() => document.getElementById(`edit-${kind}-${id}`)?.focus(), 0);

  const addAbsence = async () => {
    try {
      await api("/api/absences", { method: "POST", body: JSON.stringify(absDraft) });
      setAbsDraft({ person: "", starts_on: "", ends_on: "", kind: "pto" });
      load();
    } catch (e) {
      alert(String(e));
    }
  };

  const deleteAbsence = async (id: number) => {
    try {
      await api(`/api/absences/${id}`, { method: "DELETE" });
      load();
    } catch (e) {
      alert(String(e));
    }
  };

  const patchRow = async (entity: "tasks" | "milestones", id: number, fields: Record<string, string>) => {
    try {
      await api(`/api/${entity}/${id}`, { method: "PATCH", body: JSON.stringify(fields) });
      setEditing(null);
      refocusEdit(entity === "tasks" ? "task" : "milestone", id);
      load();
    } catch (e) {
      alert(String(e));
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
      load();
    } catch (e) {
      alert(String(e));
    }
  };

  const deleteNote = async (id: number) => {
    try {
      await api(`/api/notes/${id}`, { method: "DELETE" });
      setDeletingNote(null);
      load();
    } catch (e) {
      alert(String(e));
    }
  };

  const assignTo = async (qid: number, who: string) => {
    try {
      await api(`/api/questions/${qid}`, {
        method: "PATCH",
        body: JSON.stringify({ assigned_to: who }),
      });
      setAssigning(null);
      load();
    } catch (e) {
      alert(String(e));
    }
  };

  if (error) {
    return (
      <main className="mx-auto max-w-3xl p-8 text-sm text-danger">
        Could not reach the backend at {API_URL} — is it running? ({error})
      </main>
    );
  }

  if (Object.keys(data).length === 0)
    return <main className="p-8 text-sm text-ink-3">{loadingLine()}</main>;

  return (
    <main className="mx-auto max-w-6xl p-6">
      <SectionTabs set="work" />
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
      {pulse && (
        <section className="rounded-xl border border-line bg-card p-4 shadow-card md:col-span-2 loom-band">
          <h2 className="mb-3 font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-thread">
            Season {pulse.season.label}
            <span className="ml-2 font-normal normal-case text-ink-3">
              {pulse.season.days_left} days left
            </span>
          </h2>
          <div className="grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
            <div>
              <p className="font-display text-[30px]/none font-semibold text-ink">
                {pulse.standup_chain.chain}
                <span className="ml-1 text-sm font-normal text-ink-3">days</span>
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
                {pulse.season_totals.blockers_spotted}
                <span className="ml-1 text-sm font-normal text-ink-3">
                  / {pulse.season_totals.blockers_open} open
                </span>
              </p>
              <span
                aria-hidden
                className={`my-1.5 block h-0.5 w-6 rounded-full ${pulse.season_totals.blockers_open > 0 ? "bg-danger" : "bg-line-strong"}`}
              />
              <p className="text-xs text-ink-3">blockers spotted — spotting one is a win</p>
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
                .map((s) => `${s.impact} — avg ${s.avg_hours}h (fastest ${s.best_hours}h)`)
                .join(" · ")}
            </p>
          )}
        </section>
      )}
      <Section
        title="Engagements"
        rows={data.engagements ?? []}
        empty="No engagements — accept a request (Inbox → Requests) or start one from a playbook."
        render={(e) => (
          <li key={e.id} className="flex flex-wrap items-start justify-between gap-3 text-sm">
            <span className="min-w-0">
              <span className="flex items-center gap-2">
                <span className="font-mono text-xs text-ink-3">#{e.id}</span>
                <span className="truncate font-medium">{e.name}</span>
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
                  onClick={() => setClosing(Number(e.id))}
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
                onKeyDown={(ev) => ev.key === "Escape" && setClosing(null)}
              >
                <span className="text-ink-3">How did it end?</span>
                {CONCLUSIONS.map((c, ci) => (
                  <button
                    key={c}
                    autoFocus={ci === 0}
                    onClick={async () => {
                      try {
                        await api(`/api/engagements/${e.id}`, {
                          method: "PATCH",
                          body: JSON.stringify({ status: "closed", conclusion: c }),
                        });
                        setClosing(null);
                        load();
                      } catch (err) {
                        alert(String(err));
                      }
                    }}
                    title={CONCLUSION_HINTS[c]}
                    className="rounded bg-raised px-2 py-0.5 hover:bg-line"
                  >
                    {c}
                  </button>
                ))}
                <button onClick={() => setClosing(null)} className="text-ink-3 hover:text-ink">
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
          <li key={b.id} className="flex items-center justify-between gap-2 text-sm">
            <span>
              <span className="text-ink-3">#{b.id}</span> {b.title}
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
          <li key={String(c.person)} className="flex items-center justify-between text-sm">
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
          PTO zeroes someone out of capacity and the weekly plan; on-call and
          focus are advisory context for staffing calls.
        </p>
        <div className="mb-3 flex flex-wrap items-end gap-1.5 text-xs">
          <PersonInput
            aria-label="Who is away"
            value={absDraft.person}
            onChange={(e) => setAbsDraft({ ...absDraft, person: e.target.value })}
            placeholder="who"
            className="w-28 rounded-lg border border-line-strong bg-transparent px-2 py-1 outline-none focus:border-thread-solid"
          />
          <input
            type="date"
            aria-label="Away from"
            value={absDraft.starts_on}
            onChange={(e) => setAbsDraft({ ...absDraft, starts_on: e.target.value })}
            className="rounded-lg border border-line-strong bg-transparent px-2 py-1 outline-none focus:border-thread-solid"
          />
          <input
            type="date"
            aria-label="Away until"
            value={absDraft.ends_on}
            onChange={(e) => setAbsDraft({ ...absDraft, ends_on: e.target.value })}
            className="rounded-lg border border-line-strong bg-transparent px-2 py-1 outline-none focus:border-thread-solid"
          />
          <select
            aria-label="Kind of absence"
            value={absDraft.kind}
            onChange={(e) => setAbsDraft({ ...absDraft, kind: e.target.value })}
            className="rounded-lg border border-line-strong bg-card px-1.5 py-1"
          >
            <option value="pto">PTO</option>
            <option value="oncall">on-call</option>
            <option value="focus">focus</option>
          </select>
          <button
            disabled={!absDraft.person.trim() || !absDraft.starts_on || !absDraft.ends_on}
            onClick={addAbsence}
            className="rounded-lg bg-thread-solid px-2.5 py-1 font-medium text-white hover:opacity-90 disabled:opacity-40"
          >
            Add
          </button>
        </div>
        {(data.absences ?? []).length === 0 ? (
          <p className="text-sm text-ink-3">Nobody is scheduled away.</p>
        ) : (
          <ul className="space-y-1 text-sm">
            {(data.absences ?? []).map((a) => (
              <li key={a.id} className="flex items-center justify-between gap-2">
                <span>
                  {a.person}
                  <span className="ml-2 text-xs text-ink-3">
                    {a.kind} · {a.starts_on} → {a.ends_on}
                    {a.note ? ` · ${a.note}` : ""}
                  </span>
                </span>
                <button
                  aria-label={`Remove ${a.person}'s ${a.kind} ${a.starts_on}`}
                  onClick={() => deleteAbsence(Number(a.id))}
                  className="shrink-0 rounded bg-raised px-2 py-0.5 text-xs text-ink-2 hover:bg-line"
                >
                  remove
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
      <Section
        title="Milestones"
        rows={data.milestones ?? []}
        empty="No milestones yet — ask the agent to plan a project."
        render={(m) =>
          editing?.kind === "milestone" && editing.id === m.id ? (
            <EditRow
              key={m.id}
              fields={{ title: String(m.title), due_date: String(m.due_date ?? "") }}
              onSave={(f) => patchRow("milestones", Number(m.id), f)}
              onCancel={() => {
                setEditing(null);
                refocusEdit("milestone", Number(m.id));
              }}
            />
          ) : (
            <li key={m.id} className="flex items-center justify-between gap-2 text-sm">
              <span>
                <span className="text-ink-3">#{m.id}</span> {m.title}
                {m.due_date ? (
                  <span className="ml-2 text-xs text-ink-3">due {m.due_date}</span>
                ) : null}
              </span>
              <span className="flex items-center gap-1">
                <button
                  id={`edit-milestone-${m.id}`}
                  aria-label={`Edit milestone #${m.id}: ${m.title}`}
                  onClick={() => setEditing({ kind: "milestone", id: Number(m.id) })}
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
        empty="No open tasks — press ⌘K and type 'todo: …'."
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
            <li key={t.id} className="flex items-center justify-between gap-2 text-sm">
              <span>
                <span className="text-ink-3">#{t.id}</span> {t.title}
                {t.assignee ? (
                  <span className="ml-2 text-xs text-ink-3">@{t.assignee}</span>
                ) : null}
              </span>
              <span className="flex items-center gap-1">
                <button
                  id={`edit-task-${t.id}`}
                  aria-label={`Edit task #${t.id}: ${t.title}`}
                  onClick={() => setEditing({ kind: "task", id: Number(t.id) })}
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
        title="Open questions"
        rows={data.questions ?? []}
        empty="No questions logged."
        render={(q) => (
          <li key={q.id} className="text-sm">
            <div className="flex items-center justify-between gap-2">
              <span>
                <span className="text-ink-3">#{q.id}</span> {q.question}
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
                        const who = (ev.target as HTMLInputElement).value.trim();
                        if (ev.key === "Enter" && who) assignTo(Number(q.id), who);
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
                    <button onClick={() => setAssigning(null)} className="hover:text-ink">
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
                        const answer = (ev.target as HTMLInputElement).value.trim();
                        if (ev.key !== "Enter" || !answer) return;
                        try {
                          await api(`/api/questions/${q.id}/answer`, {
                            method: "POST",
                            body: JSON.stringify({ answer }),
                          });
                          setAnswering(null);
                          load();
                        } catch (e) {
                          alert(String(e));
                        }
                      }}
                      className="w-64 rounded-lg border border-line-strong bg-transparent px-2 py-0.5 outline-none focus:border-thread-solid"
                    />
                    <button onClick={() => setAnswering(null)} className="hover:text-ink">
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
            {d.decision !== d.title && (
              <p className="text-xs text-ink-3">{d.decision}</p>
            )}
          </li>
        )}
      />
      <StandupCard rows={data.standups ?? []} />
      <Section
        title="Calendar"
        rows={data.events ?? []}
        empty="Nothing scheduled — ask the chat agent to schedule an event."
        render={(e) => (
          <li key={e.id} className="flex items-center justify-between text-sm">
            <span>{e.title}</span>
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
                <span className="font-medium">{n.topic}</span>
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
                        className="rounded bg-danger px-2 py-0.5 text-xs font-medium text-white hover:opacity-90"
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
            <span className="font-medium text-ink-2">
              {a.actor}
            </span>{" "}
            {String(a.action).replace("_", " ")} {a.detail}
            <time dateTime={String(a.created_at)} title={String(a.created_at)} className="ml-1 text-ink-3">{timeAgo(String(a.created_at))}</time>
          </li>
        )}
      />
      </div>
    </main>
  );
}
