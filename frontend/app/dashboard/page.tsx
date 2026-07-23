"use client";

import { useEffect, useState } from "react";

import { API_URL } from "@/lib/config";

type Row = Record<string, string | number | null>;

const STATUS_COLORS: Record<string, string> = {
  planned: "bg-zinc-200 text-zinc-700",
  todo: "bg-zinc-200 text-zinc-700",
  in_progress: "bg-blue-100 text-blue-700",
  blocked: "bg-red-100 text-red-700",
  done: "bg-green-100 text-green-700",
  open: "bg-amber-100 text-amber-700",
  answered: "bg-green-100 text-green-700",
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
    ];
    Promise.all(
      endpoints.map(async (e) => {
        const res = await fetch(`${API_URL}/api/${e}`);
        if (!res.ok) throw new Error(`${e}: ${res.status}`);
        return [e, await res.json()] as const;
      }),
    )
      .then((pairs) => setData(Object.fromEntries(pairs)))
      .catch((err) => setError(String(err)));
  }, []);

  if (error) {
    return (
      <main className="mx-auto max-w-3xl p-8 text-sm text-red-600">
        Could not reach the backend at {API_URL} — is it running? ({error})
      </main>
    );
  }

  return (
    <main className="mx-auto grid max-w-6xl grid-cols-1 gap-4 p-6 md:grid-cols-2">
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
