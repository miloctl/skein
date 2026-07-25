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
  planned: "bg-zinc-200 text-zinc-700",
  todo: "bg-zinc-200 text-zinc-700",
  in_progress: "bg-blue-100 text-blue-700",
  blocked: "bg-red-100 text-red-700",
  done: "bg-green-100 text-green-700",
  open: "bg-amber-100 text-amber-700",
  answered: "bg-green-100 text-green-700",
  escalated: "bg-red-100 text-red-700",
  resolved: "bg-green-100 text-green-700",
  active: "bg-blue-100 text-blue-700",
  proposed: "bg-zinc-200 text-zinc-700",
  closing: "bg-amber-100 text-amber-700",
  closed: "bg-zinc-200 text-zinc-500",
};

function Badge({ value }: { value: string }) {
  return (
    <span
      className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${
        STATUS_COLORS[value] ?? "bg-zinc-200 text-zinc-700"
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
    <section className="rounded-xl border border-zinc-200 bg-white p-4 shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-zinc-500">
        {title}
      </h2>
      {rows.length === 0 ? (
        <p className="text-sm text-zinc-400">{empty}</p>
      ) : (
        <ul className="space-y-2">{rows.map((r) => render(r))}</ul>
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
    Promise.all(
      endpoints.map(async (e) => [e, await api<Row[]>(`/api/${e}`)] as const),
    )
      .then((pairs) => setData(Object.fromEntries(pairs)))
      .catch((err) => setError(String(err)));
    api<Pulse>("/api/pulse")
      .then(setPulse)
      .catch(() => {}); // pulse is decorative — its failure must not blank the page
  }, []);

  if (error) {
    return (
      <main className="mx-auto max-w-3xl p-8 text-sm text-red-600">
        Could not reach the backend at {API_URL} — is it running? ({error})
      </main>
    );
  }

  if (Object.keys(data).length === 0)
    return <main className="p-8 text-sm text-zinc-400">{loadingLine()}</main>;

  return (
    <main className="mx-auto grid max-w-6xl grid-cols-1 gap-4 p-6 md:grid-cols-2">
      {pulse && (
        <section className="rounded-xl border border-indigo-200 bg-indigo-50/50 p-4 shadow-sm md:col-span-2 dark:border-indigo-900 dark:bg-indigo-950/30">
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-indigo-500">
            Team pulse · season {pulse.season.label}
            <span className="ml-2 font-normal normal-case text-zinc-400">
              {pulse.season.days_left} days left
            </span>
          </h2>
          <div className="grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
            <div>
              <p className="text-2xl font-bold">
                {pulse.standup_chain.chain}
                <span className="ml-1 text-sm font-normal text-zinc-400">days</span>
              </p>
              <p className="text-xs text-zinc-500">standup chain (whole team)</p>
            </div>
            <div>
              <p className="text-2xl font-bold">
                {pulse.season_totals.engagements_shipped}
              </p>
              <p className="text-xs text-zinc-500">shipped this season</p>
            </div>
            <div>
              <p className="text-2xl font-bold">
                {pulse.season_totals.blockers_spotted}
                <span className="ml-1 text-sm font-normal text-zinc-400">
                  / {pulse.season_totals.blockers_open} open
                </span>
              </p>
              <p className="text-xs text-zinc-500">blockers spotted (spotting scores!)</p>
            </div>
            <div>
              <p className="text-2xl font-bold">{pulse.season_totals.lessons_recorded}</p>
              <p className="text-xs text-zinc-500">lessons recorded</p>
            </div>
          </div>
          {pulse.blocker_speedrun.length > 0 && (
            <p className="mt-3 text-xs text-zinc-500">
              ⏱️ Blocker speedruns:{" "}
              {pulse.blocker_speedrun
                .map((s) => `${s.impact} avg ${s.avg_hours}h (best ${s.best_hours}h)`)
                .join(" · ")}
            </p>
          )}
        </section>
      )}
      <Section
        title="Engagements"
        rows={data.engagements ?? []}
        empty="No engagements — accept an intake request or instantiate a playbook."
        render={(e) => (
          <li key={e.id} className="flex items-center justify-between gap-2 text-sm">
            <span>
              <span className="text-zinc-400">#{e.id}</span>{" "}
              {e.kind === "experiment" ? "🧪 " : ""}
              {e.name}
              <span className="ml-2 text-xs text-zinc-400">[{e.project_class}]</span>
              {e.lead ? (
                <span className="ml-2 text-xs text-zinc-400">lead @{e.lead}</span>
              ) : null}
              {e.kind === "experiment" && e.timebox_end ? (
                <span className="ml-2 text-xs text-amber-600">
                  timebox → {String(e.timebox_end)}
                </span>
              ) : null}
              {e.conclusion ? (
                <span className="ml-2 text-xs text-zinc-400">({String(e.conclusion)})</span>
              ) : null}
            </span>
            <span className="flex items-center gap-2">
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
                  className="rounded bg-zinc-100 px-2 py-0.5 text-xs text-zinc-600 hover:bg-zinc-200 dark:bg-zinc-800 dark:text-zinc-300"
                >
                  close…
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
              <span className="text-zinc-400">#{b.id}</span> {b.title}
              <span className="ml-2 text-xs text-zinc-400">
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
              <span className="ml-2 text-xs text-zinc-400">{c.detail}</span>
            </span>
            <span
              className={`text-xs font-semibold ${
                Number(c.total_percent) > 100 ? "text-red-600" : "text-green-600"
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
              <span className="text-zinc-400">#{m.id}</span> {m.title}
              {m.due_date ? (
                <span className="ml-2 text-xs text-zinc-400">due {m.due_date}</span>
              ) : null}
            </span>
            <Badge value={String(m.status)} />
          </li>
        )}
      />
      <Section
        title="Tasks"
        rows={data.tasks ?? []}
        empty="No tasks yet."
        render={(t) => (
          <li key={t.id} className="flex items-center justify-between gap-2 text-sm">
            <span>
              <span className="text-zinc-400">#{t.id}</span> {t.title}
              {t.assignee ? (
                <span className="ml-2 text-xs text-zinc-400">@{t.assignee}</span>
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
                <span className="text-zinc-400">#{q.id}</span> {q.question}
              </span>
              <Badge value={String(q.status)} />
            </div>
            {q.answer ? (
              <p className="mt-1 text-xs text-zinc-500">↳ {q.answer}</p>
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
            <p className="text-xs text-zinc-500">{d.decision}</p>
          </li>
        )}
      />
      <Section
        title="Standups"
        rows={data.standups ?? []}
        empty="No standups posted."
        render={(s) => (
          <li key={s.id} className="text-sm">
            <span className="font-medium">{s.author}</span>
            <p className="text-xs text-zinc-500">
              {s.today}
              {s.blockers ? ` · ⛔ ${s.blockers}` : ""}
            </p>
          </li>
        )}
      />
      <Section
        title="Calendar"
        rows={data.events ?? []}
        empty="Nothing scheduled."
        render={(e) => (
          <li key={e.id} className="flex items-center justify-between text-sm">
            <span>{e.title}</span>
            <span className="text-xs text-zinc-400">{e.starts_at}</span>
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
            <p className="line-clamp-2 text-xs text-zinc-500">{n.content}</p>
          </li>
        )}
      />
      <Section
        title="Recent activity"
        rows={data.activity ?? []}
        empty="No activity yet."
        render={(a) => (
          <li key={a.id} className="text-xs text-zinc-500">
            <span className="font-medium text-zinc-700 dark:text-zinc-300">
              {a.actor}
            </span>{" "}
            {String(a.action).replace("_", " ")} {a.detail}
            <span className="ml-1 text-zinc-400">{a.created_at}</span>
          </li>
        )}
      />
    </main>
  );
}
