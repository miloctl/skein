"use client";

import { useEffect, useState } from "react";

import { api, loadError } from "@/lib/api";
import { Card } from "@/components/card";
import { SectionTabs } from "@/components/section-tabs";

type Finding = {
  id: number;
  rule_id: string;
  severity: "high" | "medium" | "low" | "positive";
  message: string;
  n: number | null;
  window: string;
  week: string;
  receipt: Record<string, unknown>;
  disposition: string;
};

type Insights = {
  mttr: {
    window_days: number;
    current: { n: number; median_hours: number | null; p85_hours: number | null };
    previous: { n: number; median_hours: number | null; p85_hours: number | null };
  };
  automation_ratio: {
    month: string;
    human: number;
    agent: number;
    agent_verified: number;
    total: number;
    automation_share: number | null;
  }[];
  review_trend: {
    month: string;
    proposed: number;
    approved: number;
    rejected: number;
    avg_review_hours: number | null;
  }[];
  intake_funnel: {
    window_weeks: number;
    submitted: number;
    accepted: number;
    deferred: number;
    declined: number;
    median_days_to_disposition: number | null;
    dispositioned_n: number;
  };
  token_spend_weekly: { week: string; tokens: number }[];
  adoption: {
    weekly_active_users: number;
    team_humans: number;
    non_web_share: number | null;
    by_surface: { surface: string; users: number; actions: number }[];
  };
  findings: Finding[];
  rule_stats: {
    rule_id: string;
    fired: number;
    dispositioned: number;
    converted: number;
    dismissed: number;
    median_days_to_disposition: number | null;
  }[];
  pulse_tally: { week: string; up: number; down: number }[];
};

const SEV = {
  high: "🔴",
  medium: "🟡",
  low: "·",
  positive: "🟢",
};


function Bar({ share }: { share: number }) {
  return (
    <span className="inline-block h-2 w-28 overflow-hidden rounded bg-raised align-middle">
      <span
        className="block h-2 rounded bg-thread-solid"
        style={{ width: `${Math.min(100, Math.round(share * 100))}%` }}
      />
    </span>
  );
}

export default function InsightsPage() {
  const [d, setD] = useState<Insights | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () =>
    api<Insights>("/api/insights").then(setD).catch((e) => setError(loadError(e)));
  useEffect(() => {
    load();
  }, []);

  // inline follow-up for actions that need one more piece of information —
  // no browser prompt() anywhere on this page
  const [ask, setAsk] = useState<{ id: number; kind: "dismissed" | "deferred" } | null>(null);
  const [askValue, setAskValue] = useState("");

  const disposition = async (
    id: number,
    d: string,
    extra: { reason?: string; deferred_until?: string } = {},
  ) => {
    try {
      await api(`/api/findings/${id}/disposition`, {
        method: "POST",
        body: JSON.stringify({
          disposition: d,
          reason: extra.reason ?? "",
          deferred_until: extra.deferred_until ?? "",
        }),
      });
      setAsk(null);
      setAskValue("");
      load();
    } catch (e) {
      alert(String(e));
    }
  };

  const convert = async (id: number, kind: "task" | "question") => {
    try {
      await api(`/api/findings/${id}/convert`, {
        method: "POST",
        body: JSON.stringify({ kind }),
      });
      load();
    } catch (e) {
      alert(String(e));
    }
  };

  if (error)
    return (
      <main
        id="content"
        tabIndex={-1}
        className="mx-auto w-full max-w-5xl p-4 sm:p-6 xl:max-w-6xl"
      >
        <SectionTabs set="work" />
        <p className="text-sm text-danger">
          {error}
        </p>
      </main>
    );
  if (!d)
    return (
      <main id="content" tabIndex={-1} className="mx-auto w-full max-w-5xl xl:max-w-6xl p-4 sm:p-6">
        <SectionTabs set="work" />
        <h1 className="mb-1 font-display text-[24px]/[1.15] font-semibold tracking-[-0.01em] text-ink">
          Insights
        </h1>
        <p className="mb-6 max-w-3xl text-sm text-ink-3">Reading the tea leaves…</p>
      </main>
    );

  const m = d.mttr;
  const smallN = m.current.n < 8 || m.previous.n < 8;

  return (
    <main id="content" tabIndex={-1} className="mx-auto w-full max-w-5xl xl:max-w-6xl p-4 sm:p-6">
      <SectionTabs set="work" />
      <h1 className="mb-1 font-display text-[24px]/[1.15] font-semibold tracking-[-0.01em] text-ink">Insights</h1>
      <p className="mb-6 max-w-3xl text-sm text-ink-3">
        Everything on this page measures the system — rules, jobs, funnels —
        never individual people.
      </p>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
      <Card title="Findings — click one for its evidence">
        {d.findings.length === 0 ? (
          <p className="text-sm text-ink-3">
            Nothing to report — silence is a valid output. Findings appear here
            (and in the digest) as real usage accrues.
          </p>
        ) : (
          <ul className="space-y-2 text-sm">
            {d.findings.map((f) => (
              <li key={f.id} className={f.disposition ? "opacity-60" : ""}>
                <button
                  onClick={() => setOpen(open === f.id ? null : f.id)}
                  aria-expanded={open === f.id}
                  aria-controls={`receipt-${f.id}`}
                  className="text-left"
                >
                  {SEV[f.severity] ?? "·"} {f.message}{" "}
                  <span className="text-xs text-ink-3">
                    {f.week}
                    {f.n ? ` · n=${f.n}` : ""}
                  </span>
                  {f.disposition && (
                    <span className="ml-1.5 rounded-full bg-raised px-1.5 py-px font-mono text-[10px] text-ink-2">
                      {f.disposition}
                    </span>
                  )}
                </button>
                {open === f.id && (
                  <>
                    <pre
                      id={`receipt-${f.id}`}
                      className="mt-1 max-h-48 overflow-auto rounded bg-raised p-2 text-xs"
                    >
                      {JSON.stringify(f.receipt, null, 1)}
                    </pre>
                    {f.disposition ? null : (
                    <div className="mt-1 flex flex-wrap items-center gap-2 text-xs">
                      <button
                        onClick={() => convert(f.id, "task")}
                        className="rounded bg-thread-solid px-2 py-1 font-medium text-white hover:opacity-90"
                      >
                        → task
                      </button>
                      <button
                        onClick={() => convert(f.id, "question")}
                        className="rounded bg-thread-solid/80 px-2 py-1 font-medium text-white hover:opacity-90"
                      >
                        → question
                      </button>
                      <button
                        onClick={() => disposition(f.id, "resolved")}
                        className="rounded bg-raised px-2 py-1"
                      >
                        resolved
                      </button>
                      <button
                        onClick={() => {
                          setAsk({ id: f.id, kind: "deferred" });
                          setAskValue("");
                        }}
                        className="rounded bg-raised px-2 py-1"
                      >
                        defer…
                      </button>
                      <button
                        onClick={() => {
                          setAsk({ id: f.id, kind: "dismissed" });
                          setAskValue("");
                        }}
                        className="rounded bg-raised px-2 py-1"
                      >
                        dismiss…
                      </button>
                    </div>
                    )}
                    {ask?.id === f.id && (
                      <div className="mt-1.5 flex items-center gap-1.5 text-xs">
                        <input
                          autoFocus
                          name={ask.kind === "deferred" ? "defer-until" : "dismiss-reason"}
                          type={ask.kind === "deferred" ? "date" : "text"}
                          aria-label={
                            ask.kind === "deferred" ? "Defer until date" : "Reason for dismissing"
                          }
                          placeholder={
                            ask.kind === "deferred"
                              ? undefined
                              : "why dismiss? (false positive, known, …)"
                          }
                          value={askValue}
                          onChange={(e) => setAskValue(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Escape") setAsk(null);
                            if (e.key === "Enter" && askValue)
                              disposition(
                                f.id,
                                ask.kind,
                                ask.kind === "deferred"
                                  ? { deferred_until: askValue }
                                  : { reason: askValue },
                              );
                          }}
                          className="rounded-lg border border-line-strong bg-transparent px-2 py-1 outline-none focus:border-thread-solid"
                        />
                        <button
                          disabled={!askValue}
                          onClick={() =>
                            disposition(
                              f.id,
                              ask.kind,
                              ask.kind === "deferred"
                                ? { deferred_until: askValue }
                                : { reason: askValue },
                            )
                          }
                          className="rounded bg-thread-solid px-2 py-1 font-medium text-white disabled:opacity-40"
                        >
                          {ask.kind === "deferred" ? "defer" : "dismiss"}
                        </button>
                        <button onClick={() => setAsk(null)} className="rounded bg-raised px-2 py-1">
                          cancel
                        </button>
                      </div>
                    )}
                  </>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="Rule follow-through — do finding rules earn action?">
        {(d.rule_stats ?? []).length === 0 ? (
          <p className="text-sm text-ink-3">No findings fired yet.</p>
        ) : (
          <ul className="space-y-1 text-xs text-ink-3">
            {d.rule_stats.map((r) => (
              <li key={r.rule_id}>
                <code>{r.rule_id}</code>: fired {r.fired} · acted on{" "}
                {r.dispositioned} · converted {r.converted} · dismissed{" "}
                {r.dismissed}
                {r.median_days_to_disposition !== null &&
                  ` · median ${r.median_days_to_disposition}d to act`}
                {r.fired >= 3 && r.dismissed === r.dispositioned && r.dismissed > 0 && (
                  <span className="ml-1 text-weld">
                    · mostly dismissed — retire this rule?
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="Weekly check-in — team tally">
        {(d.pulse_tally ?? []).length === 0 ? (
          <p className="text-sm text-ink-3">
            No votes yet. The Monday digest asks the question. The 👍/👎 buttons
            are on My Day.
          </p>
        ) : (
          <ul className="space-y-1 text-sm">
            {d.pulse_tally.map((w) => (
              <li key={w.week}>
                {w.week}: 👍 {w.up} · 👎 {w.down}
                <span className="ml-2 text-xs text-ink-3">
                  {w.up + w.down > 0 && w.down > w.up
                    ? "Skein is adding effort — worth a retro"
                    : ""}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="Adoption — the tool's reach">
        <p className="text-sm">
          {d.adoption.weekly_active_users}/{d.adoption.team_humans} humans active
          this week
          {d.adoption.non_web_share !== null && (
            <span className="ml-2 text-xs text-ink-3">
              · {Math.round(d.adoption.non_web_share * 100)}% of actions outside
              the web UI (bar: &gt;50%)
            </span>
          )}
        </p>
        <ul className="mt-2 space-y-1 text-xs text-ink-3">
          {d.adoption.by_surface.map((s) => (
            <li key={s.surface}>
              {s.surface}: {s.actions} actions · {s.users} user(s)
            </li>
          ))}
          {d.adoption.by_surface.length === 0 && <li>no telemetry yet</li>}
        </ul>
      </Card>

      <Card title={`Blocker clear time — rolling ${m.window_days} days`}>
        <p className="text-sm">
          median{" "}
          <b>{m.current.median_hours !== null ? `${m.current.median_hours}h` : "—"}</b>{" "}
          · P85 {m.current.p85_hours !== null ? `${m.current.p85_hours}h` : "—"}
          <span className="ml-2 text-xs text-ink-3">n={m.current.n}</span>
        </p>
        <p className="text-xs text-ink-3">
          prior window: median{" "}
          {m.previous.median_hours !== null ? `${m.previous.median_hours}h` : "—"} (n=
          {m.previous.n})
        </p>
        {smallN && (
          <p className="mt-1 text-xs text-weld">
            Too few blockers for a trend claim (n&lt;8) — numbers shown, verdict
            withheld.
          </p>
        )}
      </Card>

      <Card title="Automation ratio — share of writes made by agents">
        <p className="mb-2 text-xs text-ink-3">
          Read it next to the rejection rate below — volume only counts if
          quality holds.
        </p>
        {d.automation_ratio.length === 0 ? (
          <p className="text-sm text-ink-3">No records yet.</p>
        ) : (
          <ul className="space-y-1 text-sm">
            {d.automation_ratio.map((r) => (
              <li key={r.month} className="flex items-center gap-2">
                <span className="w-16 text-xs text-ink-3">{r.month}</span>
                {r.automation_share !== null && <Bar share={r.automation_share} />}
                <span className="text-xs">
                  {r.automation_share !== null
                    ? `${Math.round(r.automation_share * 100)}% agent-written`
                    : "—"}{" "}
                  <span className="text-ink-3">of {r.total}</span>
                </span>
              </li>
            ))}
          </ul>
        )}
        <ul className="mt-2 space-y-1 text-xs text-ink-3">
          {d.review_trend.map((r) => (
            <li key={r.month}>
              {r.month}: {r.proposed} proposed · {r.approved} approved ·{" "}
              {r.rejected} rejected
              {r.avg_review_hours !== null && ` · ${r.avg_review_hours}h avg review`}
            </li>
          ))}
        </ul>
      </Card>

      <Card title={`Intake funnel (${d.intake_funnel.window_weeks}w)`}>
        <p className="text-sm">
          {d.intake_funnel.submitted ?? 0} submitted → {d.intake_funnel.accepted ?? 0}{" "}
          accepted · {d.intake_funnel.deferred ?? 0} deferred ·{" "}
          {d.intake_funnel.declined ?? 0} declined
        </p>
        <p className="text-xs text-ink-3">
          median {d.intake_funnel.median_days_to_disposition ?? "—"} days to
          disposition (n={d.intake_funnel.dispositioned_n})
        </p>
      </Card>

      <Card title="Token spend by week">
        {d.token_spend_weekly.length === 0 ? (
          <p className="text-sm text-ink-3">No model usage recorded.</p>
        ) : (
          <ul className="space-y-1 text-sm">
            {d.token_spend_weekly.map((w) => (
              <li key={w.week}>
                <span className="text-xs text-ink-3">{w.week}</span>{" "}
                {w.tokens.toLocaleString()} tokens
              </li>
            ))}
          </ul>
        )}
      </Card>
      </div>
    </main>
  );
}
