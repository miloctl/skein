"use client";

import { useCallback, useEffect, useState } from "react";

import { actionError, api } from "@/lib/api";
import { dismissStatus, reportStatus } from "@/lib/status";
import { ManageToggle, useManageMode } from "@/components/manage-toggle";
import { Card } from "@/components/card";
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
  // forge_url: written only by the forge webhook, so it is absent on every
  // task nobody pushed for. weekly.py selects t.*, so it is on the wire.
  tasks: {
    id: number;
    title: string;
    status: string;
    assignee: string;
    forge_url?: string;
  }[];
};

type Draft = {
  week: string;
  items: { task_id: number; title: string; assignee: string }[];
  skipped_absent?: { person: string; away_days: number }[];
};

type Forecast = {
  basis: { milestones_measured: number; median_slip_days: number };
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


export default function Portfolio() {
  const [health, setHealth] = useState<Health[] | null>(null);
  // null until loaded, like every other card here: [] renders the verdict
  // "Nobody is over 100%" during the first paint and after a failed fetch
  const [conflicts, setConflicts] = useState<Conflict[] | null>(null);
  const [flow, setFlow] = useState<Flow | null>(null);
  const [week, setWeek] = useState<Week | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [forecast, setForecast] = useState<Forecast | null>(null);
  const [commitments, setCommitments] = useState<Commitment[] | null>(null);
  const [readout, setReadout] = useState<string | null>(null);
  const [ritualOut, setRitualOut] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Three states per card, not two. A card whose fetch FAILED is still null,
  // so a null-means-loading check leaves it saying "Loading…" forever — a
  // claim that work is in progress after the work stopped. The toast alone
  // does not cover it: six loads share one region, so a dead backend names
  // one card and leaves five lying.
  const [errors, setErrors] = useState<Record<string, string>>({});
  const manage = useManageMode();

  const load = useCallback(() => {
    const fail = (key: string, what: string) => (e: Error) => {
      setErrors((cur) => ({ ...cur, [key]: `Cannot load ${what}. ${actionError(e)}` }));
      reportStatus(`Cannot load ${what}. ${actionError(e)}`);
    };
    const ok = <T,>(set: (v: T) => void, key: string) => (v: T) => {
      set(v);
      setErrors((cur) => (key in cur ? { ...cur, [key]: "" } : cur));
    };
    api<Health[]>("/api/portfolio/health")
      .then(ok(setHealth, "health"))
      .catch(fail("health", "engagement health"));
    api<Conflict[]>("/api/portfolio/conflicts")
      .then(ok(setConflicts, "conflicts"))
      .catch(fail("conflicts", "capacity conflicts"));
    api<Flow>("/api/portfolio/flow").then(ok(setFlow, "flow")).catch(fail("flow", "flow"));
    api<Week>("/api/week").then(ok(setWeek, "week")).catch(fail("week", "this week's plan"));
    api<Forecast>("/api/portfolio/forecast")
      .then(ok(setForecast, "forecast"))
      .catch(fail("forecast", "the slip forecast"));
    api<Commitment[]>("/api/commitments")
      .then(ok(setCommitments, "commitments"))
      .catch(fail("commitments", "commitments"));
  }, []);

  /** What a card shows before its data arrives: the failure if there was
   *  one, otherwise Loading. Never "Loading…" for a fetch that already
   *  failed. */
  const pending = (key: string) =>
    errors[key] ? (
      <p className="text-sm text-danger">{errors[key]}</p>
    ) : (
      <p className="text-sm text-ink-3">Loading…</p>
    );

  useEffect(load, [load]);

  // Mutations: never silent — failures land in the status region, and every
  // mutation re-fetches so the page shows reality, which can be a teammate's
  // concurrent edit rather than this tab's own write.
  const mutate = useCallback(
    (p: Promise<unknown>) => {
      setBusy(true);
      dismissStatus();
      return p
        .catch((e) => reportStatus(actionError(e)))
        .finally(() => {
          setBusy(false);
          load();
        });
    },
    [load],
  );

  return (
    <main id="content" tabIndex={-1} className="mx-auto w-full max-w-5xl xl:max-w-6xl p-4 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <SectionTabs set="work" />
        <ManageToggle />
      </div>
      <h1 className="mb-1 font-display text-[24px]/[1.15] font-semibold tracking-[-0.01em] text-ink">Health</h1>
      <p className="mb-6 max-w-3xl text-sm text-ink-3">
        Engagement health, this week&apos;s plan, flow, and forecasts — evidence
        behind every rating.
      </p>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
      <Card title="Engagement health — each rating shows why">
        {health === null ? (
          pending("health")
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
                  <span role="img" aria-label={`health ${h.health}`} title={h.health}>
                    {DOT[h.health]}
                  </span>
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

      <Card title={week ? `This week's plan — ${week.week}` : "This week's plan"}>
        <p className="mb-2 text-xs text-ink-3">
          The tasks the team promised to finish this week.
        </p>
        {week === null ? (
          pending("week")
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
                <li
                  key={t.id}
                  className={`break-words ${t.status === "done" ? "text-ink-3 line-through" : ""}`}
                >
                  #{t.id} {t.title}
                  <span className="ml-1 text-xs text-ink-3">@{t.assignee || "unassigned"}</span>
                  {/* the only surface a merged task's pull request has: Browse
                      drops a task the moment it is done, which is exactly when
                      the forge stores the PR link */}
                  {t.forge_url ? (
                    <a
                      href={String(t.forge_url)}
                      target="_blank"
                      rel="noopener noreferrer"
                      aria-label={`Code for task #${t.id}: ${t.title} (opens a new tab)`}
                      // inline-block: a done row is struck through, and an
                      // ancestor's line-through paints over descendants —
                      // the merged pull request is the most live thing here
                      className="ml-2 inline-block text-xs text-ink-3 underline hover:text-ink-2"
                    >
                      code <span aria-hidden>↗</span>
                    </a>
                  ) : null}
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
                .catch((e) => reportStatus(actionError(e)))
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
              {busy
                ? "Planning…"
                : `Add ${draft.items.length} task${draft.items.length === 1 ? "" : "s"} to the plan`}
            </button>
          )}
        </div>
        {draft && (
          <ul className="mt-2 space-y-1 text-xs text-ink-3">
            {(draft.skipped_absent ?? []).map((s) => (
              <li key={s.person} className="text-weld">
                {s.person} skipped — away {s.away_days} weekday{s.away_days === 1 ? "" : "s"} that week
              </li>
            ))}
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
        {conflicts === null ? (
          pending("conflicts")
        ) : conflicts.length === 0 ? (
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
        {flow === null ? (
          pending("flow")
        ) : (
          <div className="space-y-2 text-sm">
            <p>
              {flow.cycle_time.tasks_done} task{flow.cycle_time.tasks_done === 1 ? "" : "s"} done in 8 weeks
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
        {forecast === null ? (
          pending("forecast")
        ) : (
          <>
            <p className="mb-2 text-xs text-ink-3">
              Based on {forecast.basis.milestones_measured} completed milestone
              {forecast.basis.milestones_measured === 1 ? "" : "s"}, median slip{" "}
              {forecast.basis.median_slip_days}d.
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

      <Card title="Promises — external + yours to the team">
        {!manage && commitments?.some((c) => c.status === "open") && (
          <p className="mb-2 text-xs text-ink-3">
            To mark a promise kept or missed, turn on <b>manager
            controls</b> (top right).
          </p>
        )}
        {commitments === null ? (
          pending("commitments")
        ) : commitments.length === 0 ? (
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
                          className="rounded bg-raised px-2 py-1.5 md:py-0.5 text-xs hover:bg-line disabled:opacity-50"
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
        <Card title="Week rituals">
          <p className="mb-2 text-xs text-ink-3">
            Monday brief (everyone gets their own obligations) and Friday
            close-out (what the week leaves dangling). The scheduler runs
            these weekly. Run one now to send it and read the summary.
          </p>
          <div className="flex gap-2">
            {(["week-open", "week-close"] as const).map((r) => (
              <button
                key={r}
                disabled={busy}
                onClick={() => {
                  dismissStatus();
                  setBusy(true);
                  api<{ markdown: string }>(`/api/rituals/${r}`, { method: "POST" })
                    .then((res) => setRitualOut(res.markdown))
                    .catch((e) => reportStatus(actionError(e)))
                    .finally(() => setBusy(false));
                }}
                className="rounded-lg bg-raised px-3 py-1 text-xs font-medium text-ink-2 hover:bg-line disabled:opacity-50"
              >
                {r === "week-open" ? "Run Monday brief" : "Run Friday close-out"}
              </button>
            ))}
          </div>
          <div aria-live="polite">
            {ritualOut && (
              <pre className="mt-3 max-h-96 overflow-auto whitespace-pre-wrap rounded-lg bg-raised p-3 text-xs">
                {ritualOut}
              </pre>
            )}
          </div>
        </Card>
      )}
      {manage && (
        <Card title="Exec readout">
          <button
            disabled={busy}
            onClick={() => {
              dismissStatus();
              setBusy(true);
              api<{ markdown: string }>("/api/portfolio/readout", { method: "POST" })
                .then((r) => setReadout(r.markdown))
                .catch((e) => reportStatus(actionError(e)))
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
