"use client";

import { useCallback, useEffect, useState } from "react";

import { api } from "@/lib/api";
import { ManageToggle, useManageMode } from "@/components/manage-toggle";
import { SectionTabs } from "@/components/section-tabs";

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
  audience: "external" | "team";
};

const DOT = { red: "🔴", yellow: "🟡", green: "🟢" };

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-line bg-card p-4 shadow-card">
      <h2 className="mb-3 font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-ink-3">
        {title}
      </h2>
      {children}
    </section>
  );
}

export default function Portfolio() {
  const [health, setHealth] = useState<Health[] | null>(null);
  const [conflicts, setConflicts] = useState<Conflict[]>([]);
  const [flow, setFlow] = useState<Flow | null>(null);
  const [week, setWeek] = useState<Week | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [forecast, setForecast] = useState<Forecast | null>(null);
  const [commitments, setCommitments] = useState<Commitment[]>([]);
  const [readout, setReadout] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const manage = useManageMode();

  const load = useCallback(() => {
    api<Health[]>("/api/portfolio/health")
      .then(setHealth)
      .catch((e) => setBanner(`Load failed: ${e.message ?? e}`));
    api<Conflict[]>("/api/portfolio/conflicts").then(setConflicts).catch(() => {});
    api<Flow>("/api/portfolio/flow").then(setFlow).catch(() => {});
    api<Week>("/api/week").then(setWeek).catch(() => {});
    api<Forecast>("/api/portfolio/forecast").then(setForecast).catch(() => {});
    api<Commitment[]>("/api/commitments").then(setCommitments).catch(() => {});
  }, []);

  useEffect(load, [load]);

  // Mutations: never silent — failures land in the banner, and we always
  // re-fetch so the page shows reality (a teammate may have won the race).
  const mutate = useCallback(
    (p: Promise<unknown>) => {
      setBusy(true);
      setBanner(null);
      return p
        .catch((e) => setBanner(`${e.message ?? e}`))
        .finally(() => {
          setBusy(false);
          load();
        });
    },
    [load],
  );

  return (
    <main className="mx-auto max-w-6xl p-6">
      <div className="flex items-start justify-between">
        <SectionTabs set="work" />
        <ManageToggle />
      </div>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
      {banner && (
        <div className="flex items-center justify-between rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger md:col-span-2">
          <span>{banner}</span>
          <button onClick={() => setBanner(null)} className="text-xs underline">
            dismiss
          </button>
        </div>
      )}
      <Card title="Engagement health — each rating shows why">
        {health === null ? (
          <p className="text-sm text-ink-3">Loading…</p>
        ) : health.length === 0 ? (
          <p className="text-sm text-ink-3">
            No active engagements — accept a request on Inbox → Requests to
            start one.
          </p>
        ) : (
          <ul className="space-y-3">
            {health.map((h) => (
              <li key={h.id} className="text-sm">
                <div className="flex items-center gap-2">
                  <span>{DOT[h.health]}</span>
                  <span className="font-medium">{h.name}</span>
                  <span className="text-xs text-ink-3">
                    {h.status} · lead {h.lead || "unset"}
                  </span>
                </div>
                {h.receipts.length > 0 && (
                  <ul className="ml-6 mt-1 list-disc text-xs text-ink-3">
                    {h.receipts.map((r, i) => (
                      <li key={i}>{r}</li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title={`This week's plan — ${week?.week ?? ""}`}>
        <p className="mb-2 text-xs text-ink-3">
          The tasks the team promised to finish this week.
        </p>
        {week === null ? (
          <p className="text-sm text-ink-3">Loading…</p>
        ) : week.committed > 0 ? (
          <>
            <p className="mb-2 text-sm">
              {week.done}/{week.committed} done
              {week.kept_percent !== null && (
                <span className="ml-2 text-xs text-ink-3">({week.kept_percent}%)</span>
              )}
            </p>
            <ul className="space-y-1 text-sm">
              {week.tasks.map((t) => (
                <li key={t.id} className={t.status === "done" ? "text-ink-3 line-through" : ""}>
                  #{t.id} {t.title}
                  <span className="ml-1 text-xs text-ink-3">@{t.assignee || "unassigned"}</span>
                </li>
              ))}
            </ul>
          </>
        ) : (
          <p className="text-sm text-ink-3">Nothing committed this week yet.</p>
        )}
        <div className="mt-3 flex gap-2">
          <button
            onClick={() =>
              api<Draft>("/api/week/draft")
                .then(setDraft)
                .catch((e) => setBanner(`${e.message ?? e}`))
            }
            className="rounded-lg bg-raised px-3 py-1 text-xs font-medium hover:bg-line"
          >
            Draft a plan
          </button>
          {draft && draft.items.length > 0 && (
            <button
              disabled={busy}
              onClick={() =>
                mutate(
                  api("/api/week/plan", {
                    method: "POST",
                    body: JSON.stringify({
                      week: draft.week,
                      task_ids: draft.items.map((i) => i.task_id),
                    }),
                  }),
                ).then(() => setDraft(null))
              }
              className="rounded-lg bg-thread-solid px-3 py-1 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
            >
              {busy ? "Planning…" : `Add ${draft.items.length} task(s) to the plan`}
            </button>
          )}
        </div>
        {draft && (
          <ul className="mt-2 space-y-1 text-xs text-ink-3">
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
          <p className="text-sm text-ink-3">Nobody is over 100%.</p>
        ) : (
          <ul className="space-y-2 text-sm">
            {conflicts.map((c) => (
              <li key={c.person} className="flex items-center justify-between">
                <span>
                  {c.person}
                  <span className="ml-2 text-xs text-ink-3">{c.detail}</span>
                </span>
                <span className="text-xs font-semibold text-danger">{c.total_percent}%</span>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="Flow — cycle time from real task history">
        {flow && (
          <div className="space-y-2 text-sm">
            <p>
              {flow.cycle_time.tasks_done} tasks done in 8 weeks
              {flow.cycle_time.tasks_done > 0 && (
                <span className="text-xs text-ink-3">
                  {" "}
                  · median {flow.cycle_time.median_days}d · avg {flow.cycle_time.avg_days}d
                </span>
              )}
            </p>
            <p className="text-xs text-ink-3">
              WIP:{" "}
              {flow.wip_by_person.map((w) => `${w.person} ${w.in_progress}`).join(" · ") ||
                "none"}
            </p>
            {flow.stale_wip.length > 0 && (
              <div>
                <p className="text-xs font-medium text-weld">Stale in-progress:</p>
                <ul className="ml-4 list-disc text-xs text-ink-3">
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
            <p className="mb-2 text-xs text-ink-3">
              Based on {forecast.basis.milestones_measured} completed milestone(s), avg slip{" "}
              {forecast.basis.avg_slip_days}d.
            </p>
            {forecast.forecasts.length === 0 ? (
              <p className="text-sm text-ink-3">No dated open milestones.</p>
            ) : (
              <ul className="space-y-1 text-sm">
                {forecast.forecasts.map((f) => (
                  <li key={f.milestone_id}>
                    {f.at_risk ? "⚠️ " : ""}
                    {f.title}
                    <span className="ml-2 text-xs text-ink-3">
                      due {f.due_date} → likely {f.forecast_date}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </Card>

      <Card title="Commitments — external + yours to the team">
        {!manage && commitments.some((c) => c.status === "open") && (
          <p className="mb-2 text-xs text-ink-3">
            Marking kept/missed lives behind <b>manager controls</b> (top
            right).
          </p>
        )}
        {commitments.length === 0 ? (
          <p className="text-sm text-ink-3">
            None recorded — capture one with “promised: …”.
          </p>
        ) : (
          <ul className="space-y-2 text-sm">
            {commitments.map((c) => (
              <li key={c.id} className="flex items-center justify-between gap-2">
                <span className={c.status !== "open" ? "text-ink-3 line-through" : ""}>
                  {c.audience === "team" ? "🤝 " : ""}
                  {c.promise}
                  <span className="ml-2 text-xs text-ink-3">
                    {c.audience === "team" && "promise to the team · "}
                    {c.to_whom && `to ${c.to_whom}`} {c.due_date && `· due ${c.due_date}`}
                  </span>
                </span>
                {c.status === "open" ? (
                  manage ? (
                    <span className="flex gap-1">
                      {(["kept", "missed"] as const).map((s) => (
                        <button
                          key={s}
                          disabled={busy}
                          onClick={() =>
                            mutate(
                              api(`/api/commitments/${c.id}/status`, {
                                method: "POST",
                                body: JSON.stringify({ status: s }),
                              }),
                            )
                          }
                          className="rounded bg-raised px-2 py-0.5 text-xs hover:bg-line disabled:opacity-50"
                        >
                          mark {s}
                        </button>
                      ))}
                    </span>
                  ) : (
                    <span className="rounded-full bg-raised px-2 py-0.5 text-xs text-ink-3">
                      open
                    </span>
                  )
                ) : (
                  <span
                    className={
                      "rounded-full px-2 py-0.5 text-xs " +
                      (c.status === "kept"
                        ? "bg-ok/15 text-ok"
                        : c.status === "missed"
                          ? "bg-danger/15 text-danger"
                          : "bg-raised text-ink-3")
                    }
                  >
                    {c.status}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      {manage && (
        <Card title="Exec readout">
          <button
            disabled={busy}
            onClick={() => {
              setBusy(true);
              api<{ markdown: string }>("/api/portfolio/readout", { method: "POST" })
                .then((r) => setReadout(r.markdown))
                .catch((e) => setBanner(`${e.message ?? e}`))
                .finally(() => setBusy(false));
            }}
            className="rounded-lg bg-thread-solid px-3 py-1 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
          >
            {busy ? "Working…" : "Generate readout"}
          </button>
          {readout && (
            <pre className="mt-3 max-h-96 overflow-auto whitespace-pre-wrap rounded-lg bg-raised p-3 text-xs">
              {readout}
            </pre>
          )}
        </Card>
      )}
      </div>
    </main>
  );
}
