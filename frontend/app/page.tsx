"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { api } from "@/lib/api";

type Row = Record<string, string | number | null>;

type Briefing = {
  user: string;
  date: string;
  needs_you: {
    open_questions: Row[];
    pending_reviews: Row[];
    your_blockers: Row[];
    intake_to_triage: Row[];
    notifications: Row[];
  };
  your_work: { tasks: Row[]; due_soon: Row[] };
  team: { escalated_blockers: Row[]; todays_events: Row[]; recent_activity: Row[] };
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

export default function MyDay() {
  const [b, setB] = useState<Briefing | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api<Briefing>("/api/briefing").then(setB).catch((e) => setError(String(e)));
  }, []);
  useEffect(load, [load]);

  const resolveBlocker = async (id: number) => {
    try {
      await api(`/api/blockers/${id}/resolve`, {
        method: "POST",
        body: JSON.stringify({ resolution: "resolved from My Day" }),
      });
    } catch (e) {
      alert(String(e));
    }
    load();
  };

  if (error)
    return (
      <main className="mx-auto max-w-3xl p-8 text-sm text-red-600">
        Could not reach the backend — is it running? ({error})
      </main>
    );
  if (!b) return <main className="p-8 text-sm text-zinc-400">Loading…</main>;

  const n = b.needs_you;
  const needsCount =
    n.open_questions.length +
    n.pending_reviews.length +
    n.your_blockers.length +
    (n.notifications ?? []).length;

  return (
    <main className="mx-auto w-full max-w-5xl p-6">
      <h1 className="mb-1 text-xl font-bold">
        Good day, {b.user === "anonymous" ? "there" : b.user} 👋
      </h1>
      <p className="mb-6 text-sm text-zinc-500">
        {b.date} ·{" "}
        {needsCount === 0
          ? "nothing is waiting on you"
          : `${needsCount} thing${needsCount > 1 ? "s" : ""} need${needsCount > 1 ? "" : "s"} you`}
        {b.user === "anonymous" ? " · set your name (top right) to personalize" : ""}
      </p>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <Card title="Needs you">
          <ul className="space-y-2 text-sm">
            {n.open_questions.map((q) => (
              <li key={`q${q.id}`}>
                ❓ <span className="text-zinc-400">#{q.id}</span> {q.question}{" "}
                <span className="text-xs text-zinc-400">from {q.asked_by}</span>
              </li>
            ))}
            {n.your_blockers.map((bl) => (
              <li key={`b${bl.id}`} className="flex items-center justify-between gap-2">
                <span>
                  ⛔ <span className="text-zinc-400">#{bl.id}</span> {bl.title}{" "}
                  <span className="text-xs text-zinc-400">({bl.impact})</span>
                </span>
                <button
                  onClick={() => resolveBlocker(Number(bl.id))}
                  className="rounded bg-green-100 px-2 py-0.5 text-xs font-medium text-green-700 hover:bg-green-200"
                >
                  resolve
                </button>
              </li>
            ))}
            {n.pending_reviews.length > 0 && (
              <li>
                📥 <Link href="/review" className="underline">
                  {n.pending_reviews.length} pending review
                  {n.pending_reviews.length > 1 ? "s" : ""}
                </Link>{" "}
                awaiting a human
              </li>
            )}
            {n.intake_to_triage.length > 0 && (
              <li>
                📨 <Link href="/intake" className="underline">
                  {n.intake_to_triage.length} intake request
                  {n.intake_to_triage.length > 1 ? "s" : ""}
                </Link>{" "}
                to triage
              </li>
            )}
            {(n.notifications ?? []).map((nt) => (
              <li key={`n${nt.id}`} className="flex items-center justify-between gap-2">
                <span>🔔 {nt.message}</span>
                <button
                  onClick={async () => {
                    try {
                      await api("/api/notifications/read", {
                        method: "POST",
                        body: JSON.stringify({ notification_id: nt.id }),
                      });
                    } catch (e) {
                      alert(String(e));
                    }
                    load();
                  }}
                  className="rounded bg-zinc-200 px-2 py-0.5 text-xs text-zinc-600 hover:bg-zinc-300"
                >
                  dismiss
                </button>
              </li>
            ))}
            {needsCount === 0 &&
              n.intake_to_triage.length === 0 &&
              (n.notifications ?? []).length === 0 && (
                <li className="text-zinc-400">All clear. 🎉</li>
              )}
          </ul>
        </Card>

        <Card title="Your work">
          <ul className="space-y-2 text-sm">
            {b.your_work.tasks.map((t) => (
              <li key={t.id}>
                <span className="text-zinc-400">#{t.id}</span> {t.title}{" "}
                <span className="text-xs text-zinc-400">
                  [{t.priority}/{t.status}]
                </span>
              </li>
            ))}
            {b.your_work.tasks.length === 0 && (
              <li className="text-zinc-400">No tasks assigned to you.</li>
            )}
            {b.your_work.due_soon.length > 0 && (
              <li className="pt-1 text-xs text-amber-600">
                ⏰ Due within a week:{" "}
                {b.your_work.due_soon.map((t) => `#${t.id} ${t.title}`).join(" · ")}
              </li>
            )}
          </ul>
        </Card>

        <Card title="Team pulse">
          <ul className="space-y-2 text-sm">
            {b.team.escalated_blockers.map((e) => (
              <li key={e.id} className="text-red-600">
                🚨 Escalated: #{e.id} {e.title} (owner: {e.owner || "unowned"})
              </li>
            ))}
            {b.team.todays_events.map((e) => (
              <li key={e.id}>
                📅 {String(e.starts_at).slice(11, 16)} {e.title}
              </li>
            ))}
            {b.team.escalated_blockers.length === 0 &&
              b.team.todays_events.length === 0 && (
                <li className="text-zinc-400">No escalations, nothing scheduled today.</li>
              )}
          </ul>
        </Card>

        <Card title="Since yesterday">
          <ul className="space-y-1">
            {b.team.recent_activity.slice(0, 12).map((a) => (
              <li key={a.id} className="text-xs text-zinc-500">
                <span className="font-medium text-zinc-700 dark:text-zinc-300">
                  {a.actor}
                </span>{" "}
                {String(a.action).replace(/_/g, " ")} {a.detail}
              </li>
            ))}
            {b.team.recent_activity.length === 0 && (
              <li className="text-xs text-zinc-400">Quiet so far.</li>
            )}
          </ul>
        </Card>
      </div>
    </main>
  );
}
