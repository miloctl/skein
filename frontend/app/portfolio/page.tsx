"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import Link from "next/link";

import { actionError, api } from "@/lib/api";
import { HASH_TARGET, useHashTarget } from "@/lib/hash-target";
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

/** Cycle time for reading. The service rounds to 0.1 day, so same-day work
 *  arrived as 0.0 and the headline read "median 0d · avg 0d" — a broken-
 *  looking claim about the team's fastest weeks. Under a day, hours; a 0.0
 *  is anything under 72 minutes, so it says "under 2h" rather than a zero. */
function cycleTime(days: number | null): string {
  if (days === null) return "—";
  if (days >= 1) return `${days}d`;
  const hours = Math.round(days * 24);
  return hours > 0 ? `${hours}h` : "under 2h";
}

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
  // the Monday job's plan for this week, when one sits unjudged in Approvals
  // (weekly.py). The card offers the review instead of the drafter then —
  // drafting again files a second proposal for the same week.
  pending_proposal?: { id: number; summary: string } | null;
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

type PromiseRow = {
  id: number;
  promise: string;
  to_whom: string;
  due_date: string | null;
  status: string;
  audience: "external" | "team";
};

const HEALTH_TONE = { red: "bg-danger", yellow: "bg-weld", green: "bg-ok" };

/** Weekly throughput, one row per week. `flow_metrics` has returned this
 *  series since it shipped and nothing read it, so cycle time answered "how
 *  long does one task take" while "how many land per week" had no surface.
 *
 *  Bars are scaled to the busiest week in the window, never to a fixed
 *  ceiling: throughput has no target here, and a bar drawn against an
 *  invented maximum reads as progress toward a goal nobody set. */
function Throughput({ weeks }: { weeks: Record<string, number> }) {
  const rows = Object.entries(weeks);
  if (rows.length === 0)
    return (
      // said, not omitted: a card that simply drops the section reads as a
      // rendering fault next to the cycle-time line above it
      <p className="text-xs text-ink-3">No task finished in the last 8 weeks.</p>
    );
  const peak = Math.max(...rows.map(([, n]) => n));
  return (
    <div>
      <h3 className="text-xs uppercase tracking-wide text-ink-3">
        Finished per week
      </h3>
      {/* the bars carry no number a screen reader can use, and the scale is
          the whole point of them — say it once, in text */}
      <p className="text-xs text-ink-3">Each bar is drawn against the busiest week.</p>
      <ul className="mt-1 space-y-0.5">
        {rows.map(([week, n]) => (
          <li key={week} className="flex items-center gap-2 text-xs">
            <span className="w-16 shrink-0 text-ink-3">{week}</span>
            <span
              className="inline-block h-2 w-28 shrink-0 overflow-hidden rounded bg-raised align-middle"
              aria-hidden
            >
              <span
                className="block h-2 rounded bg-thread-solid"
                // peak is >= 1 whenever a row exists, so this never divides
                // by zero: a week with no finished task is absent from the
                // series rather than present as 0.
                style={{ width: `${Math.round((n / peak) * 100)}%` }}
              />
            </span>
            <span className="tabular-nums">
              {n} task{n === 1 ? "" : "s"}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}


type Usage = {
  models: {
    model_id: string;
    calls: number;
    input_tokens: number;
    output_tokens: number;
    cost_usd: number | null;
    unpriced_calls: number;
  }[];
  engagements: {
    engagement: string;
    engagement_id: number | null;
    calls: number;
    input_tokens: number;
    output_tokens: number;
    cost_usd: number | null;
    unpriced_calls: number;
  }[];
  month: {
    month: string;
    cost_usd: number | null;
    unpriced_calls: number;
    calls: number;
    budget_usd: number | null;
  };
  prices_error: string;
};

export default function Portfolio() {
  const [health, setHealth] = useState<Health[] | null>(null);
  // null until loaded, like every other card here: [] renders the verdict
  // "Nobody is over 100%" during the first paint and after a failed fetch
  const [conflicts, setConflicts] = useState<Conflict[] | null>(null);
  const [flow, setFlow] = useState<Flow | null>(null);
  const [week, setWeek] = useState<Week | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [forecast, setForecast] = useState<Forecast | null>(null);
  const [promises, setPromises] = useState<PromiseRow[] | null>(null);
  const [usage, setUsage] = useState<Usage | null>(null);
  // the artifact each button FILED, not its body: the markdown now has a
  // reader (Work → Reports), and a <pre> dump beside the button was the one
  // place it was ever shown formatted-as-source
  const [readout, setReadout] = useState<number | null>(null);
  const [ritualOut, setRitualOut] = useState<{
    artifactId: number | null;
    skipped: boolean;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  // a ref beside the state: the click guards below read it synchronously, and
  // a second click inside the same tick would otherwise see the stale value
  const busyRef = useRef(false);
  useEffect(() => {
    busyRef.current = busy;
  }, [busy]);
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
    api<PromiseRow[]>("/api/promises")
      .then(ok(setPromises, "promises"))
      .catch(fail("promises", "promises"));
    api<Usage>("/api/usage").then(ok(setUsage, "usage")).catch(fail("usage", "AI usage"));
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
  // `#promise-7` from My Day, the manager queue and the delta brief — the
  // rows arrive from the fetch above, so the fragment alone scrolls nowhere.
  useHashTarget(promises);

  // Mutations: never silent — failures land in the status region, and every
  // mutation re-fetches so the page shows reality, which can be a teammate's
  // concurrent edit rather than this tab's own write.
  // `busy` no longer DISABLES a control: disabling the element that has focus
  // blurs it, and re-enabling never brings focus back — a keyboard reader was
  // dropped to the top of the document on every action. The guard moved into
  // the handlers, and the buttons carry aria-busy instead.
  const mutate = useCallback(
    (p: Promise<unknown>) => {
      if (busyRef.current) return Promise.resolve();
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
        behind every health call.
      </p>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
      <Card title="Engagement health — each call shows why">
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
                  <span
                    aria-hidden
                    className={`size-2.5 shrink-0 rounded-full ${HEALTH_TONE[h.health]}`}
                  />
                  <span className="text-xs font-medium text-ink-2">{h.health}</span>
                  {/* the name is the way in to the whole engagement: this
                      card shows the call and its receipts, the brief shows
                      everything that produced them */}
                  <Link
                    href={`/engagement/${h.id}`}
                    className="font-medium hover:underline"
                  >
                    {h.name}
                  </Link>
                  <span className="text-xs text-ink-3">
                    {h.status} · lead {h.lead || "unset"}
                  </span>
                </div>
                {h.receipts.length > 0 ? (
                  <ul className="ml-6 mt-1 list-disc text-xs text-ink-3">
                    {h.receipts.map((r, i) => (
                      <li key={i}>{r}</li>
                    ))}
                  </ul>
                ) : (
                  /* the card title promises a why for every health call,
                     and an empty receipt list IS the why: services/portfolio.py
                     rates green exactly when no signal fired */
                  <p className="ml-6 mt-1 text-xs text-ink-3">
                    Nothing flagged: no overdue milestone, no open blocker, no
                    stalled or waiting task, and no silent stretch.
                  </p>
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
        {/* a plan proposal already waiting supersedes the drafter: drafting
            again files a SECOND proposal for the same week, and the reviewer
            gets two commitment lines to untangle */}
        {week?.pending_proposal ? (
          <p className="mt-3 text-sm">
            <Link
              href={`/review?id=${week.pending_proposal.id}`}
              className="underline decoration-line-strong underline-offset-2 hover:decoration-ink-3"
            >
              Proposal #{week.pending_proposal.id}
            </Link>{" "}
            already proposes this week&apos;s plan. Review it in Inbox →
            Approvals.
          </p>
        ) : (
        <>
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
              aria-busy={busy}
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
        </>
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

      <Card title="AI usage and estimated cost">
        {/* The budget finding pointed people at a raw JSON endpoint. Spend
            belongs beside engagement health because that is where the
            question is asked: what is the AI layer costing, and on what. */}
        {errors.usage ? (
          <p className="text-sm text-danger">{errors.usage}</p>
        ) : /* a payload with no month is a payload this card cannot read.
               Reaching into it renders nothing and throws instead, and an
               exception here takes down HEALTH, CONFLICTS and the week plan
               with it — one card must never cost the page. */
        !usage?.month ? (
          <p className="text-sm text-ink-3">Loading…</p>
        ) : usage.month.calls === 0 ? (
          <p className="text-sm text-ink-3">
            No AI model calls were recorded this month.
          </p>
        ) : (
          <>
            <p className="text-sm">
              {usage.month.month}: {usage.month.calls.toLocaleString()} call
              {usage.month.calls === 1 ? "" : "s"}
              {usage.month.cost_usd !== null ? (
                <> · ${usage.month.cost_usd.toFixed(2)} estimated</>
              ) : null}
              {usage.month.budget_usd ? (
                <> of a ${usage.month.budget_usd.toFixed(2)} ceiling</>
              ) : null}
            </p>
            {/* An unpriced call is reported as unpriced, never as zero: a sum
                that silently omits calls reads as a total (services/usage.py) */}
            {usage.month.unpriced_calls > 0 ? (
              <p className="text-xs text-weld">
                {usage.month.unpriced_calls.toLocaleString()} call
                {usage.month.unpriced_calls === 1 ? " has" : "s have"} no price.
                The estimated cost does not include {usage.month.unpriced_calls === 1 ? "it" : "them"}.
                {/* the fix is an env var — whoever runs the server acts on
                    it, and for everyone else it is a wall of config they
                    cannot touch */}
                {manage
                  ? " Set a price for the model in SKEIN_MODELS or SKEIN_MODEL_PRICES."
                  : ""}
              </p>
            ) : null}
            <h3 className="mt-3 text-xs uppercase tracking-wide text-ink-3">
              By engagement
            </h3>
            <ul className="space-y-1 text-sm">
              {usage.engagements.map((e) => (
                <li key={e.engagement_id ?? "unlinked"}>
                  {e.engagement}: {e.calls.toLocaleString()} call
                  {e.calls === 1 ? "" : "s"} ·{" "}
                  {(e.input_tokens + e.output_tokens).toLocaleString()} tokens
                  {e.cost_usd !== null ? <> · ${e.cost_usd.toFixed(2)}</> : null}
                </li>
              ))}
            </ul>
            <h3 className="mt-3 text-xs uppercase tracking-wide text-ink-3">
              By model
            </h3>
            <ul className="space-y-1 text-sm">
              {usage.models.map((m) => (
                <li key={m.model_id}>
                  {m.model_id}: {m.calls.toLocaleString()} call
                  {m.calls === 1 ? "" : "s"} ·{" "}
                  {(m.input_tokens + m.output_tokens).toLocaleString()} tokens
                  {m.cost_usd !== null ? <> · ${m.cost_usd.toFixed(2)}</> : null}
                </li>
              ))}
            </ul>
          </>
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
                  · median {cycleTime(flow.cycle_time.median_days)} · avg{" "}
                  {cycleTime(flow.cycle_time.avg_days)}
                </span>
              )}
            </p>
            <p className="text-xs text-ink-3">
              WIP:{" "}
              {flow.wip_by_person.map((w) => `${w.person} ${w.in_progress}`).join(" · ") ||
                "none"}
            </p>
            <Throughput weeks={flow.throughput_by_week} />
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
              {/* with nothing completed the model has no slip to apply, so
                  every "likely" date below just repeats the due date — say
                  that, instead of dressing no information as a prediction */}
              {forecast.basis.milestones_measured === 0
                ? "No milestone has been completed yet, so these dates repeat the due date. They become a forecast after the team finishes some work."
                : `Based on ${forecast.basis.milestones_measured} completed milestone${
                    forecast.basis.milestones_measured === 1 ? "" : "s"
                  }, median slip ${forecast.basis.median_slip_days}d.`}
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
                      due {f.due_date}
                      {forecast.basis.milestones_measured > 0 &&
                        ` → likely ${f.forecast_date}`}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </Card>

      <Card title="Promises — external + yours to the team">
        {!manage && promises?.some((c) => c.status === "open") && (
          <p className="mb-2 text-xs text-ink-3">
            To mark a promise kept or missed, turn on <b>Management view</b>
            (top right).
          </p>
        )}
        {promises === null ? (
          pending("promises")
        ) : promises.length === 0 ? (
          <p className="text-sm text-ink-3">
            None recorded — capture one with “promised: …”.
          </p>
        ) : (
          <ul className="space-y-2 text-sm">
            {promises.map((c) => (
              <li
                key={c.id}
                id={`promise-${c.id}`}
                tabIndex={-1}
                className={`flex items-center justify-between gap-2 ${HASH_TARGET}`}
              >
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
                          aria-busy={busy}
                          onClick={() =>
                            mutate(
                              api(`/api/promises/${c.id}/status`, {
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
                aria-busy={busy}
                onClick={() => {
                  if (busyRef.current) return;
                  dismissStatus();
                  setBusy(true);
                  api<{ artifact_id: number | null; skipped?: string }>(`/api/rituals/${r}`, {
                    method: "POST",
                  })
                    .then((res) =>
                      setRitualOut({
                        artifactId: res.artifact_id,
                        skipped: Boolean(res.skipped),
                      }),
                    )
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
            {ritualOut ? (
              <div className="mt-3 text-xs">
                <p>
                  {ritualOut.skipped
                    ? "This ritual already ran this week. Skein did not send duplicate notifications."
                    : "The ritual is complete."}
                </p>
                {/* the id is null when the claim outlives its report (see
                    services/rituals.py::_existing_week_artifact) — a link to
                    `?id=null` opens Reports on an artifact that cannot load */}
                {ritualOut.artifactId === null ? (
                  <p>The report for this week is no longer stored.</p>
                ) : (
                  <Link
                    href={`/artifacts?id=${ritualOut.artifactId}`}
                    className="underline decoration-line-strong underline-offset-2 hover:decoration-ink-3"
                  >
                    {ritualOut.skipped
                      ? "Read the existing report on Work → Reports"
                      : "Read the report on Work → Reports"}
                  </Link>
                )}
              </div>
            ) : null}
          </div>
        </Card>
      )}
      {manage && (
        <Card title="Exec readout">
          <button
            aria-busy={busy}
            onClick={() => {
              if (busyRef.current) return;
              dismissStatus();
              setBusy(true);
              api<{ artifact_id: number }>("/api/portfolio/readout", { method: "POST" })
                // a same-day rerun overwrites the file and returns the SAME
                // artifact id, so setting the id alone changed no state, fired
                // no live region, and left the reader unable to tell it ran
                .then((r) => {
                  setReadout(r.artifact_id);
                  reportStatus("Exec readout generated.", "confirmation");
                })
                .catch((e) => reportStatus(actionError(e)))
                .finally(() => setBusy(false));
            }}
            className="rounded-lg bg-thread-solid px-3 py-1 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
          >
            {busy ? "Working…" : "Generate readout"}
          </button>
          <div aria-live="polite">
            {readout ? (
              <p className="mt-3 text-xs">
                <Link
                  href={`/artifacts?id=${readout}`}
                  className="underline decoration-line-strong underline-offset-2 hover:decoration-ink-3"
                >
                  Read it on Work → Reports
                </Link>
              </p>
            ) : null}
          </div>
        </Card>
      )}
      </div>
    </main>
  );
}
