"use client";

import { useEffect, useState } from "react";

import { API_URL, api } from "@/lib/api";
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
  const [yesterday, setYesterday] = useState("");
  const [today, setToday] = useState("");
  const [blockers, setBlockers] = useState("");
  const [posted, setPosted] = useState(false);

  const post = async () => {
    if (!today.trim()) return;
    try {
      await api("/api/standups", {
        method: "POST",
        body: JSON.stringify({ yesterday, today, blockers }),
      });
      setPosted(true);
      setTimeout(() => window.location.reload(), 700);
    } catch (e) {
      alert(String(e));
    }
  };

  return (
    <section className="rounded-xl border border-line bg-card p-4 shadow-card">
      <h2 className="mb-3 font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-ink-3">
        Standups
      </h2>
      <div className="mb-3 space-y-1.5">
        <input
          value={yesterday}
          onChange={(e) => setYesterday(e.target.value)}
          placeholder="yesterday (optional)"
          className="w-full rounded-lg border border-line-strong bg-transparent px-2 py-1 text-sm outline-none focus:border-thread-solid"
        />
        <input
          value={today}
          onChange={(e) => setToday(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && post()}
          placeholder="today — what are you on?"
          className="w-full rounded-lg border border-line-strong bg-transparent px-2 py-1 text-sm outline-none focus:border-thread-solid"
        />
        <div className="flex gap-1.5">
          <input
            value={blockers}
            onChange={(e) => setBlockers(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && post()}
            placeholder="blockers — auto-filed with an escalation clock"
            className="flex-1 rounded-lg border border-line-strong bg-transparent px-2 py-1 text-sm outline-none focus:border-thread-solid"
          />
          <button
            onClick={post}
            disabled={!today.trim()}
            className="rounded-lg bg-thread-solid px-3 py-1 text-sm font-medium text-white hover:opacity-90 disabled:opacity-40"
          >
            {posted ? "✓" : "Post"}
          </button>
        </div>
      </div>
      {rows.length === 0 ? (
        <p className="text-sm text-ink-3">No standups posted yet — yours can be first.</p>
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

export default function Dashboard() {
  const [data, setData] = useState<Record<string, Row[]>>({});
  const [pulse, setPulse] = useState<Pulse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
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
    <main className="mx-auto grid max-w-6xl grid-cols-1 gap-4 p-6 md:grid-cols-2">
      {pulse && (
        <section className="rounded-xl border border-line bg-card p-4 shadow-card md:col-span-2 loom-band">
          <h2 className="mb-3 font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-thread">
            Team pulse · season {pulse.season.label}
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
        empty="No engagements — accept an intake request or start one from a playbook."
        render={(e) => (
          <li key={e.id} className="flex items-start justify-between gap-3 text-sm">
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
              {e.status !== "closed" && (
                <button
                  onClick={async () => {
                    const conclusion = prompt(
                      `Close "${e.name}" — conclusion?\n(achieved / partial / missed / invalidated / unmeasured / stopped)`,
                      e.kind === "experiment" ? "invalidated" : "achieved",
                    );
                    if (!conclusion) return;
                    try {
                      await api(`/api/engagements/${e.id}`, {
                        method: "PATCH",
                        body: JSON.stringify({ status: "closed", conclusion }),
                      });
                      window.location.reload();
                    } catch (err) {
                      alert(String(err));
                    }
                  }}
                  className="whitespace-nowrap rounded bg-raised px-2 py-0.5 text-xs text-ink-2 hover:bg-line"
                >
                  close out
                </button>
              )}
              <Badge value={String(e.status)} />
            </span>
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
      <Section
        title="Milestones"
        rows={data.milestones ?? []}
        empty="No milestones yet — ask the agent to plan a project."
        render={(m) => (
          <li key={m.id} className="flex items-center justify-between gap-2 text-sm">
            <span>
              <span className="text-ink-3">#{m.id}</span> {m.title}
              {m.due_date ? (
                <span className="ml-2 text-xs text-ink-3">due {m.due_date}</span>
              ) : null}
            </span>
            <Badge value={String(m.status)} />
          </li>
        )}
      />
      <Section
        title="Tasks"
        rows={data.tasks ?? []}
        empty="No tasks yet — press ⌘K and type 'todo: …'."
        render={(t) => (
          <li key={t.id} className="flex items-center justify-between gap-2 text-sm">
            <span>
              <span className="text-ink-3">#{t.id}</span> {t.title}
              {t.assignee ? (
                <span className="ml-2 text-xs text-ink-3">@{t.assignee}</span>
              ) : null}
            </span>
            <span className="flex items-center gap-1">
              <Badge value={String(t.priority)} />
              <Badge value={String(t.status)} />
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
              </span>
              <Badge value={String(q.status)} />
            </div>
            {q.status === "open" && (
              <p className="mt-0.5 text-xs text-ink-3">
                {q.assigned_to ? (
                  <>→ @{q.assigned_to}</>
                ) : (
                  <button
                    onClick={async () => {
                      const who = prompt("Assign this question to:");
                      if (!who?.trim()) return;
                      try {
                        await api(`/api/questions/${q.id}`, {
                          method: "PATCH",
                          body: JSON.stringify({ assigned_to: who.trim() }),
                        });
                        window.location.reload();
                      } catch (e) {
                        alert(String(e));
                      }
                    }}
                    className="underline hover:text-ink-2"
                  >
                    unassigned — assign…
                  </button>
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
        render={(n) => (
          <li key={n.id} className="text-sm">
            <span className="font-medium">{n.topic}</span>
            <p className="line-clamp-2 text-xs text-ink-3">
              {/* notes hold markdown; this is a plain-text preview */}
              {String(n.content).replace(/[*#`]/g, "").replace(/\s+/g, " ")}
            </p>
          </li>
        )}
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
            <span className="ml-1 text-ink-3">{a.created_at}</span>
          </li>
        )}
      />
    </main>
  );
}
