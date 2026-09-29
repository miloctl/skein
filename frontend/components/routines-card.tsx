"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { PersonInput } from "@/components/person-input";
import { PeekLink } from "@/components/task-peek";
import { VisibilityPicker } from "@/components/visibility-picker";
import { actionError, api, loadError } from "@/lib/api";
import { ONLY_YOU, ROSTER, type TierChoice, useStrongIdentity } from "@/lib/audience";
import { reportStatus } from "@/lib/status";

/** One row of GET /api/routines (routes/api.py::_routine_view). */
export type Routine = {
  id: number;
  title: string;
  description: string;
  assignee: string;
  agent: string;
  acceptance_criteria: string;
  weekdays: string;
  at_time: string;
  every_weeks: number;
  starts_on: string;
  due_days: number | null;
  status: "active" | "paused";
  paused_reason: string;
  paused_by: string;
  last_outcome: string;
  created_by: string;
  visibility: string;
  next_local: string | null;
  last_local: string | null;
  open_task_id: number | null;
  can_edit: boolean;
  can_delete: boolean;
};

const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
// services/routines.py caps the title so " (YYYY-MM-DD)" still fits a task title
const TITLE_MAX = 187;

function listed(words: string[]): string {
  return words.length < 2 ? words.join("") : `${words.slice(0, -1).join(", ")} and ${words.at(-1)}`;
}

/** "Every 2 weeks on Wednesday at 10:00": the one wording of a schedule, for
 *  the list and for the line above Save. */
export function scheduleWords(r: { weekdays: string; at_time: string; every_weeks: number }): string {
  const days = r.weekdays
    .split(",")
    .filter(Boolean)
    .map((d) => DAYS[Number(d) - 1]);
  const every = r.every_weeks === 1 ? "Every week" : `Every ${r.every_weeks} weeks`;
  return `${every} on ${listed(days)} at ${r.at_time}`;
}

/** "Mon 5 Oct, 07:00" from the server's wall time ("2026-10-05T07:00"). The
 *  server already converted the zone, so this only reads the parts. */
function wallLabel(wall: string, withTime = true): string {
  const [day, time] = wall.split("T");
  const [y, m, d] = day.split("-").map(Number);
  const label = new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-GB", {
    weekday: "short",
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  });
  return withTime && time ? `${label}, ${time}` : label;
}

function pausedLine(r: Routine): string {
  switch (r.paused_reason) {
    case "by_person":
      return `Paused by ${r.paused_by}.`;
    case "nobody_finished":
      return "Paused after 3 skipped times, because the last task is still open.";
    case "owner_inactive":
      return `Paused because ${r.created_by} is deactivated.`;
    case "owner_left_crew":
      return `Paused because ${r.created_by} is no longer in its crew.`;
    case "agent_unavailable":
      return `Paused because agent ${r.agent} is deactivated.`;
    case "fire_refused":
      return "Paused because Skein could not create or delegate the task.";
    default:
      return "Paused.";
  }
}

function outcomeLine(r: Routine): string {
  if (!r.last_local) return "";
  const when = wallLabel(r.last_local, false);
  if (r.last_outcome === "previous_open")
    return r.open_task_id ? `Skipped ${when}: task #${r.open_task_id} is still open.` : `Skipped ${when}.`;
  if (r.last_outcome === "late") return `Last task created ${when}, late.`;
  if (r.last_outcome === "fired") return `Last task created ${when}.`;
  return "";
}

type Draft = {
  title: string;
  description: string;
  days: number[];
  at_time: string;
  every_weeks: number;
  starts_on: string;
  assignee: string;
  agent: string;
  acceptance_criteria: string;
  due_days: string;
};

const blank = (): Draft => ({
  title: "",
  description: "",
  days: [1],
  at_time: "09:00",
  every_weeks: 1,
  starts_on: "",
  assignee: "",
  agent: "",
  acceptance_criteria: "",
  due_days: "",
});

const fromRoutine = (r: Routine): Draft => ({
  title: r.title,
  description: r.description,
  days: r.weekdays.split(",").map(Number),
  at_time: r.at_time,
  every_weeks: r.every_weeks,
  starts_on: r.starts_on,
  assignee: r.assignee,
  agent: r.agent,
  acceptance_criteria: r.acceptance_criteria,
  due_days: r.due_days === null ? "" : String(r.due_days),
});

const field =
  "mt-0.5 block w-full rounded-lg border border-line-strong bg-transparent px-2 py-1 text-sm text-ink outline-none focus:border-thread-solid";

function RoutineForm({
  initial,
  editing,
  onSave,
  onCancel,
}: {
  initial: Draft;
  /** an edit never changes the tier: it is fixed when the routine is written */
  editing: boolean;
  onSave: (body: Record<string, unknown>) => Promise<boolean>;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState(initial);
  const strong = useStrongIdentity();
  const [tier, setTier] = useState<TierChoice>(strong ? ONLY_YOU : ROSTER);
  const [agents, setAgents] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const set = (patch: Partial<Draft>) => setDraft((cur) => ({ ...cur, ...patch }));

  useEffect(() => {
    // the task panel's Delegate control lists the same rows
    api<{ agent: string; delegatable: boolean }[]>("/api/agents")
      .then((rows) => setAgents(rows.filter((r) => r.delegatable).map((r) => r.agent)))
      .catch(() => setAgents([]));
  }, []);

  const schedule = { weekdays: [...draft.days].sort().join(","), at_time: draft.at_time, every_weeks: draft.every_weeks };
  const missing = !draft.title.trim() ? "Write a title." : draft.days.length === 0 ? "Pick at least one day." : "";

  const submit = async () => {
    if (busy || missing) return;
    setBusy(true);
    const body: Record<string, unknown> = {
      title: draft.title.trim(),
      description: draft.description,
      weekdays: schedule.weekdays,
      at_time: draft.at_time,
      every_weeks: draft.every_weeks,
      assignee: draft.agent ? "" : draft.assignee.trim(),
      agent: draft.agent,
      acceptance_criteria: draft.agent ? draft.acceptance_criteria : "",
      due_days: draft.due_days === "" ? null : Number(draft.due_days),
    };
    if (draft.starts_on) body.starts_on = draft.starts_on;
    if (!editing) Object.assign(body, tier);
    try {
      if (!(await onSave(body))) setBusy(false);
    } catch {
      setBusy(false);
    }
  };

  return (
    <form
      className="space-y-2 rounded-lg border border-line p-3 text-sm"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <label className="block text-xs text-ink-3">
        Title
        <input
          autoFocus
          required
          maxLength={TITLE_MAX}
          value={draft.title}
          onChange={(e) => set({ title: e.target.value })}
          className={field}
        />
      </label>
      <label className="block text-xs text-ink-3">
        Description
        <textarea
          rows={2}
          maxLength={4000}
          value={draft.description}
          onChange={(e) => set({ description: e.target.value })}
          className={field}
        />
      </label>
      <fieldset className="text-xs text-ink-3">
        <legend>Days</legend>
        <div className="mt-0.5 flex flex-wrap gap-x-3 gap-y-1">
          {DAYS.map((name, i) => (
            <label key={name} className="flex min-h-6 items-center gap-1 text-ink-2">
              <input
                type="checkbox"
                checked={draft.days.includes(i + 1)}
                onChange={(e) =>
                  set({
                    days: e.target.checked
                      ? [...draft.days, i + 1]
                      : draft.days.filter((d) => d !== i + 1),
                  })
                }
              />
              {name}
            </label>
          ))}
        </div>
      </fieldset>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
        <label className="block text-xs text-ink-3">
          Time
          <input
            type="time"
            required
            value={draft.at_time}
            onChange={(e) => set({ at_time: e.target.value })}
            aria-describedby="routine-time-hint"
            className={field}
          />
          <span id="routine-time-hint" className="mt-0.5 block">
            Skein creates the task within 5 minutes of this time.
          </span>
        </label>
        <label className="block text-xs text-ink-3">
          Repeat
          <select
            value={draft.every_weeks}
            onChange={(e) => set({ every_weeks: Number(e.target.value) })}
            className={field}
          >
            <option value={1}>every week</option>
            <option value={2}>every 2 weeks</option>
            <option value={3}>every 3 weeks</option>
            <option value={4}>every 4 weeks</option>
          </select>
        </label>
        <label className="block text-xs text-ink-3">
          Starts on
          <input
            type="date"
            value={draft.starts_on}
            onChange={(e) => set({ starts_on: e.target.value })}
            className={field}
          />
        </label>
      </div>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
        <label className="block text-xs text-ink-3">
          Agent
          <select
            value={draft.agent}
            onChange={(e) => {
              set({ agent: e.target.value });
              // a private task has one reader, and an agent is not it
              // (delegation.delegate_task), so the tier leaves private
              if (e.target.value && tier.visibility === "private") setTier(ROSTER);
            }}
            className={field}
          >
            <option value="">no agent</option>
            {[...new Set([...agents, draft.agent].filter(Boolean))].map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
        </label>
        <label className="block text-xs text-ink-3">
          Assignee
          <PersonInput
            value={draft.agent ? "" : draft.assignee}
            disabled={!!draft.agent}
            maxLength={64}
            onChange={(e) => set({ assignee: e.target.value })}
            className={field}
          />
        </label>
        <label className="block text-xs text-ink-3">
          Due after (days)
          <input
            type="number"
            min={0}
            max={27}
            value={draft.due_days}
            onChange={(e) => set({ due_days: e.target.value })}
            className={field}
          />
        </label>
      </div>
      {draft.agent ? (
        <label className="block text-xs text-ink-3">
          Definition of done for {draft.agent}
          <textarea
            rows={2}
            maxLength={1000}
            value={draft.acceptance_criteria}
            onChange={(e) => set({ acceptance_criteria: e.target.value })}
            className={field}
          />
        </label>
      ) : null}
      {editing ? null : (
        <div className="space-y-1">
          <VisibilityPicker value={tier} onChange={setTier} label="routine" allowPrivate={strong && !draft.agent} />
          {draft.agent ? (
            <p className="text-xs text-ink-3">
              An agent routine cannot be private, because an agent cannot read a private task.
            </p>
          ) : null}
        </div>
      )}
      <p className="text-xs text-ink-2">
        {draft.days.length
          ? `${scheduleWords(schedule)}${draft.starts_on ? `, starting ${draft.starts_on}` : ""}, Skein creates a new task${draft.agent ? ` and delegates it to ${draft.agent}` : ""}. If the last task is still open, Skein skips that time.`
          : "Pick at least one day."}
      </p>
      <div className="flex gap-2">
        <button
          type="submit"
          aria-disabled={busy || !!missing || undefined}
          aria-describedby={missing ? "routine-missing" : undefined}
          className="rounded-lg bg-thread-solid px-3 py-1 text-xs font-medium text-white hover:opacity-90 aria-disabled:opacity-50"
        >
          Save routine
        </button>
        <button type="button" onClick={onCancel} className="rounded-lg px-2 py-1 text-xs text-ink-3 hover:text-ink">
          Cancel
        </button>
        {missing ? (
          <span id="routine-missing" className="self-center text-xs text-ink-3">
            {missing}
          </span>
        ) : null}
      </div>
    </form>
  );
}

/** Plan the week → Routines: recurring work written once
 *  (services/routines.py, docs/intent/routines.md). */
export function RoutinesCard() {
  const [data, setData] = useState<{ zone: string; routines: Routine[] } | null>(null);
  const [failure, setFailure] = useState("");
  const [form, setForm] = useState<"new" | number | null>(null);
  const [confirming, setConfirming] = useState<number | null>(null);
  const strong = useStrongIdentity();
  const focusAfter = useRef<string | null>(null);

  const load = useCallback(() => {
    // no-store: api() caches a GET for 15 seconds, and a reread after a save
    // must show the save
    return api<{ zone: string; routines: Routine[] }>("/api/routines", { cache: "no-store" })
      .then((d) => {
        setData(d);
        setFailure("");
      })
      .catch((e) => setFailure(loadError(e)));
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const id = focusAfter.current;
    if (!id) return;
    focusAfter.current = null;
    document.getElementById(id)?.focus();
  }, [data, form, confirming]);

  const act = async (work: () => Promise<unknown>, done: string, focus: string) => {
    try {
      await work();
      reportStatus(done, "confirmation");
      focusAfter.current = focus;
      await load();
      return true;
    } catch (e) {
      reportStatus(actionError(e));
      return false;
    }
  };

  const save = async (body: Record<string, unknown>) => {
    const editing = typeof form === "number" ? form : null;
    let saved = 0;
    const ok = await act(
      async () => {
        const out = await api<{ id: number }>(editing ? `/api/routines/${editing}` : "/api/routines", {
          method: editing ? "PATCH" : "POST",
          body: JSON.stringify(body),
        });
        saved = editing ?? out.id;
      },
      editing ? "Routine saved." : "Routine saved. Skein creates its first task at the next time.",
      "",
    );
    if (ok) {
      focusAfter.current = `routine-${saved}`;
      setForm(null);
    }
    return ok;
  };

  const rows = data?.routines ?? [];
  const control = "rounded px-2 py-0.5 text-xs text-ink-3 hover:text-ink min-h-6";

  return (
    <div className="space-y-3 text-sm">
      <p className="text-xs text-ink-3">
        Work that comes back every week. Each time, Skein creates a new task, and with an agent it
        delegates the task to that agent with you as sponsor.
        {data ? ` Times are on the team clock (${data.zone}).` : ""}
      </p>
      {failure ? <p className="text-xs text-danger">{failure}</p> : null}
      {!data ? (
        failure ? null : <p className="text-xs text-ink-3">Loading…</p>
      ) : rows.length === 0 ? (
        <p className="text-xs text-ink-3">No routines yet. Write the work once, and the week brings it back.</p>
      ) : (
        <ul className="space-y-3">
          {rows.map((r) =>
            form === r.id ? (
              <li key={r.id}>
                <RoutineForm
                  initial={fromRoutine(r)}
                  editing
                  onSave={save}
                  onCancel={() => {
                    focusAfter.current = `routine-${r.id}`;
                    setForm(null);
                  }}
                />
              </li>
            ) : (
              <li key={r.id} id={`routine-${r.id}`} tabIndex={-1} className="space-y-0.5 outline-none">
                <p className="text-ink">
                  <span className="text-ink-3">#{r.id}</span> {r.title}
                </p>
                <p className="text-xs text-ink-3">
                  {scheduleWords(r)} · {r.agent ? `→ ${r.agent}` : r.assignee ? `@${r.assignee}` : "unassigned"}
                </p>
                <p className="text-xs text-ink-2">
                  {r.status === "active" && r.next_local ? `Next: ${wallLabel(r.next_local)}` : pausedLine(r)}
                </p>
                {outcomeLine(r) ? <p className="text-xs text-ink-3">{outcomeLine(r)}</p> : null}
                {r.open_task_id ? (
                  <PeekLink taskId={r.open_task_id} className="text-xs">
                    Task #{r.open_task_id}
                  </PeekLink>
                ) : null}
                {confirming === r.id ? (
                  <div key="confirm" className="flex flex-wrap items-center gap-2 text-xs">
                    <span id={`routine-confirm-${r.id}`} className="text-ink-2">
                      Delete routine #{r.id}? Its tasks stay.
                    </span>
                    <button
                      type="button"
                      aria-describedby={`routine-confirm-${r.id}`}
                      onClick={async () => {
                        setConfirming(null);
                        await act(
                          () => api(`/api/routines/${r.id}`, { method: "DELETE" }),
                          `Routine #${r.id} deleted.`,
                          "planning-routines",
                        );
                      }}
                      className="rounded bg-danger-solid px-2 py-0.5 font-medium text-white hover:opacity-90"
                    >
                      Delete routine
                    </button>
                    <button
                      type="button"
                      autoFocus
                      onClick={() => {
                        focusAfter.current = `routine-delete-${r.id}`;
                        setConfirming(null);
                      }}
                      className={control}
                    >
                      Keep
                    </button>
                  </div>
                ) : (
                  <div key="actions" className="flex flex-wrap gap-1">
                    {r.status === "active" ? (
                      <button
                        type="button"
                        onClick={() =>
                          act(
                            () => api(`/api/routines/${r.id}/pause`, { method: "POST" }),
                            `Routine #${r.id} paused.`,
                            `routine-${r.id}`,
                          )
                        }
                        className={control}
                      >
                        Pause{" "}
                        <span className="sr-only">routine #{r.id}</span>
                      </button>
                    ) : r.can_edit ? (
                      <button
                        type="button"
                        onClick={() =>
                          act(
                            () => api(`/api/routines/${r.id}/resume`, { method: "POST" }),
                            `Routine #${r.id} resumed.`,
                            `routine-${r.id}`,
                          )
                        }
                        className={control}
                      >
                        Resume{" "}
                        <span className="sr-only">routine #{r.id}</span>
                      </button>
                    ) : null}
                    {r.can_edit ? (
                      <button type="button" onClick={() => setForm(r.id)} className={control}>
                        Edit{" "}
                        <span className="sr-only">routine #{r.id}</span>
                      </button>
                    ) : null}
                    {r.can_delete ? (
                      <button
                        type="button"
                        id={`routine-delete-${r.id}`}
                        onClick={() => setConfirming(r.id)}
                        className={control}
                      >
                        Delete{" "}
                        <span className="sr-only">routine #{r.id}</span>
                      </button>
                    ) : null}
                  </div>
                )}
              </li>
            ),
          )}
        </ul>
      )}
      {form === "new" ? (
        <RoutineForm
          initial={blank()}
          editing={false}
          onSave={save}
          onCancel={() => {
            focusAfter.current = "routine-new";
            setForm(null);
          }}
        />
      ) : strong ? (
        <button
          type="button"
          id="routine-new"
          onClick={() => setForm("new")}
          className="rounded-lg bg-raised px-3 py-1 text-xs text-ink-2 hover:bg-line"
        >
          New routine
        </button>
      ) : (
        <p className="text-xs text-ink-3">
          To write a routine, sign in with a personal key in Settings. A routine creates tasks under
          your name, so Skein asks who you are first.
        </p>
      )}
    </div>
  );
}
