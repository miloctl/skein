"use client";

import { useEffect, useState } from "react";

import { api } from "@/lib/api";

type Finding = {
  id: number;
  rule_id: string;
  severity: "high" | "medium" | "low" | "positive";
  message: string;
  n: number | null;
  window: string;
  week: string;
  receipt: Record<string, unknown>;
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

function Bar({ share }: { share: number }) {
  return (
    <span className="inline-block h-2 w-28 overflow-hidden rounded bg-zinc-200 align-middle dark:bg-zinc-800">
      <span
        className="block h-2 rounded bg-indigo-500"
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
    api<Insights>("/api/insights").then(setD).catch((e) => setError(String(e)));
  useEffect(() => {
    load();
  }, []);

  const disposition = async (id: number, d: string) => {
    let reason = "";
    let deferred_until = "";
    if (d === "dismissed") {
      const answer = prompt("Why dismiss? (false positive, known, …)");
      if (answer === null) return; // cancelled — don't dismiss
      reason = answer;
    }
    if (d === "deferred") {
      const until = prompt("Defer until (YYYY-MM-DD)?");
      if (!until) return;
      deferred_until = until;
    }
    try {
      await api(`/api/findings/${id}/disposition`, {
        method: "POST",
        body: JSON.stringify({ disposition: d, reason, deferred_until }),
      });
      load();
    } catch (e) {
      alert(String(e));
    }
  };

  const convert = async (id: number) => {
    const kind = prompt("Convert to 'task' or 'question'?", "task");
    if (kind !== "task" && kind !== "question") return;
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
    return <main className="p-8 text-sm text-red-600">Backend unreachable: {error}</main>;
  if (!d) return <main className="p-8 text-sm text-zinc-400">Reading the tea leaves…</main>;

  const m = d.mttr;
  const smallN = m.current.n < 8 || m.previous.n < 8;

  return (
    <main className="mx-auto grid max-w-6xl grid-cols-1 gap-4 p-6 md:grid-cols-2">
      <Card title="Findings (receipts on click)">
        {d.findings.length === 0 ? (
          <p className="text-sm text-zinc-400">
            Nothing to report — silence is a valid output. Findings appear here
            (and in the digest) as real usage accrues.
          </p>
        ) : (
          <ul className="space-y-2 text-sm">
            {d.findings.map((f) => (
              <li key={f.id}>
                <button
                  onClick={() => setOpen(open === f.id ? null : f.id)}
                  aria-expanded={open === f.id}
                  aria-controls={`receipt-${f.id}`}
                  className="text-left"
                >
                  {SEV[f.severity] ?? "·"} {f.message}{" "}
                  <span className="text-xs text-zinc-400">
                    {f.week}
                    {f.n ? ` · n=${f.n}` : ""}
                  </span>
                </button>
                {open === f.id && (
                  <>
                    <pre
                      id={`receipt-${f.id}`}
                      className="mt-1 max-h-48 overflow-auto rounded bg-zinc-50 p-2 text-xs dark:bg-zinc-950"
                    >
                      {JSON.stringify(f.receipt, null, 1)}
                    </pre>
                    <div className="mt-1 flex gap-2 text-xs">
                      <button
                        onClick={() => convert(f.id)}
                        className="rounded bg-zinc-900 px-2 py-1 font-medium text-white dark:bg-zinc-100 dark:text-zinc-900"
                      >
                        → work item
                      </button>
                      <button
                        onClick={() => disposition(f.id, "resolved")}
                        className="rounded bg-zinc-100 px-2 py-1 dark:bg-zinc-800"
                      >
                        resolved
                      </button>
                      <button
                        onClick={() => disposition(f.id, "deferred")}
                        className="rounded bg-zinc-100 px-2 py-1 dark:bg-zinc-800"
                      >
                        defer…
                      </button>
                      <button
                        onClick={() => disposition(f.id, "dismissed")}
                        className="rounded bg-zinc-100 px-2 py-1 dark:bg-zinc-800"
                      >
                        dismiss…
                      </button>
                    </div>
                  </>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="Rule follow-through (rules, never people)">
        {(d.rule_stats ?? []).length === 0 ? (
          <p className="text-sm text-zinc-400">No findings fired yet.</p>
        ) : (
          <ul className="space-y-1 text-xs text-zinc-500">
            {d.rule_stats.map((r) => (
              <li key={r.rule_id}>
                <code>{r.rule_id}</code>: fired {r.fired} · acted on{" "}
                {r.dispositioned} · converted {r.converted} · dismissed{" "}
                {r.dismissed}
                {r.median_days_to_disposition !== null &&
                  ` · median ${r.median_days_to_disposition}d to act`}
                {r.fired >= 3 && r.dismissed === r.dispositioned && r.dismissed > 0 && (
                  <span className="ml-1 text-amber-600">· retire candidate?</span>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="Weekly pulse (team tally — never per person)">
        {(d.pulse_tally ?? []).length === 0 ? (
          <p className="text-sm text-zinc-400">
            No votes yet. The Monday digest asks; 👍/👎 lives on My Day.
          </p>
        ) : (
          <ul className="space-y-1 text-sm">
            {d.pulse_tally.map((w) => (
              <li key={w.week}>
                {w.week}: 👍 {w.up} · 👎 {w.down}
                <span className="ml-2 text-xs text-zinc-400">
                  {w.up + w.down > 0 && w.down > w.up
                    ? "Skein is adding effort — worth a retro"
                    : ""}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="Adoption (the tool's reach, not people's output)">
        <p className="text-sm">
          {d.adoption.weekly_active_users}/{d.adoption.team_humans} humans active
          this week
          {d.adoption.non_web_share !== null && (
            <span className="ml-2 text-xs text-zinc-400">
              · {Math.round(d.adoption.non_web_share * 100)}% of actions outside
              the web UI (bar: &gt;50%)
            </span>
          )}
        </p>
        <ul className="mt-2 space-y-1 text-xs text-zinc-500">
          {d.adoption.by_surface.map((s) => (
            <li key={s.surface}>
              {s.surface}: {s.actions} actions · {s.users} user(s)
            </li>
          ))}
          {d.adoption.by_surface.length === 0 && <li>no telemetry yet</li>}
        </ul>
      </Card>

      <Card title={`Blocker clear time (rolling ${m.window_days}d)`}>
        <p className="text-sm">
          median{" "}
          <b>{m.current.median_hours !== null ? `${m.current.median_hours}h` : "—"}</b>{" "}
          · P85 {m.current.p85_hours !== null ? `${m.current.p85_hours}h` : "—"}
          <span className="ml-2 text-xs text-zinc-400">n={m.current.n}</span>
        </p>
        <p className="text-xs text-zinc-500">
          prior window: median{" "}
          {m.previous.median_hours !== null ? `${m.previous.median_hours}h` : "—"} (n=
          {m.previous.n})
        </p>
        {smallN && (
          <p className="mt-1 text-xs text-amber-600">
            Too few blockers for a trend claim (n&lt;8) — numbers shown, verdict
            withheld.
          </p>
        )}
      </Card>

      <Card title="Automation ratio (read next to rejection rate)">
        {d.automation_ratio.length === 0 ? (
          <p className="text-sm text-zinc-400">No records yet.</p>
        ) : (
          <ul className="space-y-1 text-sm">
            {d.automation_ratio.map((r) => (
              <li key={r.month} className="flex items-center gap-2">
                <span className="w-16 text-xs text-zinc-400">{r.month}</span>
                {r.automation_share !== null && <Bar share={r.automation_share} />}
                <span className="text-xs">
                  {r.automation_share !== null
                    ? `${Math.round(r.automation_share * 100)}% agent-written`
                    : "—"}{" "}
                  <span className="text-zinc-400">of {r.total}</span>
                </span>
              </li>
            ))}
          </ul>
        )}
        <ul className="mt-2 space-y-1 text-xs text-zinc-500">
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
        <p className="text-xs text-zinc-500">
          median {d.intake_funnel.median_days_to_disposition ?? "—"} days to
          disposition (n={d.intake_funnel.dispositioned_n})
        </p>
      </Card>

      <Card title="Token spend by week">
        {d.token_spend_weekly.length === 0 ? (
          <p className="text-sm text-zinc-400">No model usage recorded.</p>
        ) : (
          <ul className="space-y-1 text-sm">
            {d.token_spend_weekly.map((w) => (
              <li key={w.week}>
                <span className="text-xs text-zinc-400">{w.week}</span>{" "}
                {w.tokens.toLocaleString()} tokens
              </li>
            ))}
          </ul>
        )}
      </Card>
    </main>
  );
}
