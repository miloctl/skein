"use client";

import { useCallback, useEffect, useState } from "react";

import { api } from "@/lib/api";

type Health = {
  id: number;
  name: string;
  status: string;
  lead: string;
  health: "red" | "yellow" | "green";
  receipts: string[];
};

type Conflict = { person: string; total_percent: number; detail: string };

type Flow = {
  cycle_time: { tasks_done: number; avg_days: number | null; median_days: number | null };
  throughput_by_week: Record<string, number>;
  wip_by_person: { person: string; in_progress: number }[];
  stale_wip: { id: number; title: string; assignee: string; days_stale: number }[];
};

type Week = {
  week: string;
  committed: number;
  done: number;
  kept_percent: number | null;
  tasks: { id: number; title: string; status: string; assignee: string }[];
};

type Draft = { week: string; items: { task_id: number; title: string; assignee: string }[] };

type Forecast = {
  basis: { milestones_measured: number; avg_slip_days: number };
  forecasts: {
    milestone_id: number;
    title: string;
    project: string;
    due_date: string;
    forecast_date: string;
    at_risk: boolean;
  }[];
};

type Commitment = {
  id: number;
  promise: string;
  to_whom: string;
  due_date: string | null;
  status: string;
};

const DOT = { red: "🔴", yellow: "🟡", green: "🟢" };

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-zinc-200 bg-white p-4 shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-zinc-500">
        {title}
      </h2>
      {children}
    </section>
  );
}

export default function Portfolio() {
  const [health, setHealth] = useState<Health[]>([]);
  const [conflicts, setConflicts] = useState<Conflict[]>([]);
  const [flow, setFlow] = useState<Flow | null>(null);
  const [week, setWeek] = useState<Week | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [forecast, setForecast] = useState<Forecast | null>(null);
  const [commitments, setCommitments] = useState<Commitment[]>([]);
  const [readout, setReadout] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api<Health[]>("/api/portfolio/health").then(setHealth).catch((e) => setError(String(e)));
    api<Conflict[]>("/api/portfolio/conflicts").then(setConflicts).catch(() => {});
    api<Flow>("/api/portfolio/flow").then(setFlow).catch(() => {});
    api<Week>("/api/week").then(setWeek).catch(() => {});
    api<Forecast>("/api/portfolio/forecast").then(setForecast).catch(() => {});
    api<Commitment[]>("/api/commitments").then(setCommitments).catch(() => {});
  }, []);

  useEffect(load, [load]);

  if (error)
    return <main className="p-8 text-sm text-red-600">Backend unreachable: {error}</main>;

  return (
    <main className="mx-auto grid max-w-6xl grid-cols-1 gap-4 p-6 md:grid-cols-2">
      <Card title="Engagement health (receipts shown)">
        {health.length === 0 && (
          <p className="text-sm text-zinc-400">No active engagements.</p>
        )}
        <ul className="space-y-3">
          {health.map((h) => (
            <li key={h.id} className="text-sm">
              <div className="flex items-center gap-2">
                <span>{DOT[h.health]}</span>
                <span className="font-medium">{h.name}</span>
                <span className="text-xs text-zinc-400">
                  {h.status} · lead {h.lead || "unset"}
                </span>
              </div>
              {h.receipts.length > 0 && (
                <ul className="ml-6 mt-1 list-disc text-xs text-zinc-500">
                  {h.receipts.map((r, i) => (
                    <li key={i}>{r}</li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ul>
      </Card>

      <Card title={`Commitment line — ${week?.week ?? ""}`}>
        {week && week.committed > 0 ? (
          <>
            <p className="mb-2 text-sm">
              {week.done}/{week.committed} done
              {week.kept_percent !== null && (
                <span className="ml-2 text-xs text-zinc-400">({week.kept_percent}%)</span>
              )}
            </p>
            <ul className="space-y-1 text-sm">
              {week.tasks.map((t) => (
                <li key={t.id} className={t.status === "done" ? "text-zinc-400 line-through" : ""}>
                  #{t.id} {t.title}
                  <span className="ml-1 text-xs text-zinc-400">@{t.assignee || "unassigned"}</span>
                </li>
              ))}
            </ul>
          </>
        ) : (
          <p className="text-sm text-zinc-400">Nothing committed this week yet.</p>
        )}
        <div className="mt-3 flex gap-2">
          <button
            onClick={() => api<Draft>("/api/week/draft").then(setDraft).catch(() => {})}
            className="rounded-lg bg-zinc-100 px-3 py-1 text-xs font-medium hover:bg-zinc-200 dark:bg-zinc-800 dark:hover:bg-zinc-700"
          >
            Draft a plan
          </button>
          {draft && draft.items.length > 0 && (
            <button
              onClick={() =>
                api("/api/week/plan", {
                  method: "POST",
                  body: JSON.stringify({
                    week: draft.week,
                    task_ids: draft.items.map((i) => i.task_id),
                  }),
                })
                  .then(() => {
                    setDraft(null);
                    load();
                  })
                  .catch(() => {})
              }
              className="rounded-lg bg-indigo-600 px-3 py-1 text-xs font-medium text-white hover:bg-indigo-700"
            >
              Commit {draft.items.length} task(s)
            </button>
          )}
        </div>
        {draft && (
          <ul className="mt-2 space-y-1 text-xs text-zinc-500">
            {draft.items.length === 0 && <li>Nothing to draft — assign some tasks first.</li>}
            {draft.items.map((i) => (
              <li key={i.task_id}>
                #{i.task_id} {i.title} @{i.assignee}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="Capacity conflicts">
        {conflicts.length === 0 ? (
          <p className="text-sm text-zinc-400">Nobody is over 100%.</p>
        ) : (
          <ul className="space-y-2 text-sm">
            {conflicts.map((c) => (
              <li key={c.person} className="flex items-center justify-between">
                <span>
                  {c.person}
                  <span className="ml-2 text-xs text-zinc-400">{c.detail}</span>
                </span>
                <span className="text-xs font-semibold text-red-600">{c.total_percent}%</span>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="Flow (from real timestamps)">
        {flow && (
          <div className="space-y-2 text-sm">
            <p>
              {flow.cycle_time.tasks_done} tasks done in 8 weeks
              {flow.cycle_time.tasks_done > 0 && (
                <span className="text-xs text-zinc-400">
                  {" "}
                  · median {flow.cycle_time.median_days}d · avg {flow.cycle_time.avg_days}d
                </span>
              )}
            </p>
            <p className="text-xs text-zinc-500">
              WIP:{" "}
              {flow.wip_by_person.map((w) => `${w.person} ${w.in_progress}`).join(" · ") ||
                "none"}
            </p>
            {flow.stale_wip.length > 0 && (
              <div>
                <p className="text-xs font-medium text-amber-600">Stale in-progress:</p>
                <ul className="ml-4 list-disc text-xs text-zinc-500">
                  {flow.stale_wip.map((s) => (
                    <li key={s.id}>
                      #{s.id} {s.title} — {s.days_stale}d (@{s.assignee || "unassigned"})
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </Card>

      <Card title="Slip forecast">
        {forecast && (
          <>
            <p className="mb-2 text-xs text-zinc-400">
              Based on {forecast.basis.milestones_measured} completed milestone(s), avg slip{" "}
              {forecast.basis.avg_slip_days}d.
            </p>
            {forecast.forecasts.length === 0 ? (
              <p className="text-sm text-zinc-400">No dated open milestones.</p>
            ) : (
              <ul className="space-y-1 text-sm">
                {forecast.forecasts.map((f) => (
                  <li key={f.milestone_id}>
                    {f.at_risk ? "⚠️ " : ""}
                    {f.title}
                    <span className="ml-2 text-xs text-zinc-400">
                      due {f.due_date} → likely {f.forecast_date}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </Card>

      <Card title="External commitments">
        {commitments.length === 0 ? (
          <p className="text-sm text-zinc-400">
            None recorded — capture one with “promised: …”.
          </p>
        ) : (
          <ul className="space-y-2 text-sm">
            {commitments.map((c) => (
              <li key={c.id} className="flex items-center justify-between gap-2">
                <span className={c.status !== "open" ? "text-zinc-400 line-through" : ""}>
                  {c.promise}
                  <span className="ml-2 text-xs text-zinc-400">
                    {c.to_whom && `to ${c.to_whom}`} {c.due_date && `· due ${c.due_date}`}
                  </span>
                </span>
                {c.status === "open" ? (
                  <span className="flex gap-1">
                    {(["kept", "missed"] as const).map((s) => (
                      <button
                        key={s}
                        onClick={() =>
                          api(`/api/commitments/${c.id}/status`, {
                            method: "POST",
                            body: JSON.stringify({ status: s }),
                          })
                            .then(load)
                            .catch(() => {})
                        }
                        className="rounded bg-zinc-100 px-2 py-0.5 text-xs hover:bg-zinc-200 dark:bg-zinc-800 dark:hover:bg-zinc-700"
                      >
                        {s}
                      </button>
                    ))}
                  </span>
                ) : (
                  <span className="text-xs text-zinc-400">{c.status}</span>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="Exec readout">
        <button
          onClick={() =>
            api<{ markdown: string }>("/api/portfolio/readout", { method: "POST" })
              .then((r) => setReadout(r.markdown))
              .catch(() => {})
          }
          className="rounded-lg bg-indigo-600 px-3 py-1 text-xs font-medium text-white hover:bg-indigo-700"
        >
          Generate readout
        </button>
        {readout && (
          <pre className="mt-3 max-h-96 overflow-auto whitespace-pre-wrap rounded-lg bg-zinc-50 p-3 text-xs dark:bg-zinc-950">
            {readout}
          </pre>
        )}
      </Card>
    </main>
  );
}
