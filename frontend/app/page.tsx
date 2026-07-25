"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { api, getUser } from "@/lib/api";
import { emptyState, loadingLine } from "@/lib/whimsy";

type Row = Record<string, string | number | null>;

type AttentionItem = {
  kind: string;
  ref_id: number;
  group: "decide" | "unblock" | "commit" | "review" | "notice";
  label: string;
  reason: string;
  link: string;
};

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
  attention: AttentionItem[];
  your_work: { tasks: Row[]; due_soon: Row[] };
  team: {
    recently_shipped: Row[];
    escalated_blockers: Row[];
    todays_events: Row[];
    recent_activity: Row[];
  };
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

type Onboarding = {
  steps: { id: string; label: string; done: boolean; link: string; hint: string }[];
  complete: boolean;
  progress: string;
};

export default function MyDay() {
  const [b, setB] = useState<Briefing | null>(null);
  const [onboarding, setOnboarding] = useState<Onboarding | null>(null);
  const [error, setError] = useState<string | null>(null);

  const generation = useRef(0);
  const load = useCallback(() => {
    const g = ++generation.current;
    api<Briefing>("/api/briefing")
      .then((r) => {
        if (g === generation.current) setB(r); // last request wins
      })
      .catch((e) => setError(String(e)));
    // onboarding progress is per-user — key the dismissal that way too
    const onboardKey = `skein-onboarded:${getUser()}`;
    if (window.localStorage.getItem(onboardKey) !== "1") {
      api<Onboarding>("/api/onboarding")
        .then((o) => {
          if (o.complete) window.localStorage.setItem(onboardKey, "1");
          if (g === generation.current) setOnboarding(o);
        })
        .catch(() => {});
    }
  }, []);
  useEffect(load, [load]);

  // The Ship It moment: confetti once per shipped engagement, per browser.
  useEffect(() => {
    const shipped = b?.team.recently_shipped ?? [];
    if (shipped.length === 0) return;
    let stored: unknown[] = [];
    try {
      stored = JSON.parse(window.localStorage.getItem("strands-confetti") ?? "[]");
      if (!Array.isArray(stored)) stored = [];
    } catch {
      stored = []; // corrupted storage must never crash My Day
    }
    const seen = new Set(stored);
    const fresh = shipped.filter((e) => !seen.has(e.id));
    if (fresh.length === 0) return;
    if (!window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      import("canvas-confetti").then(({ default: confetti }) => {
        confetti({ particleCount: 140, spread: 80, origin: { y: 0.6 } });
      });
    }
    fresh.forEach((e) => seen.add(e.id));
    window.localStorage.setItem("strands-confetti", JSON.stringify([...seen]));
  }, [b]);

  const patchTask = async (id: number, status: string) => {
    try {
      await api(`/api/tasks/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ status }),
      });
    } catch (e) {
      alert(String(e));
    }
    load();
  };

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
  if (!b) return <main className="p-8 text-sm text-zinc-400">{loadingLine()}</main>;

  const attention = b.attention ?? [];
  const needsCount = attention.filter((a) => a.group !== "notice").length;
  const GROUP_META: Record<AttentionItem["group"], { title: string; icon: string }> = {
    decide: { title: "Decide", icon: "⚖️" },
    unblock: { title: "Unblock", icon: "⛔" },
    commit: { title: "Commit", icon: "🤝" },
    review: { title: "Review", icon: "📥" },
    notice: { title: "Notice", icon: "🔔" },
  };

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

      {onboarding && !onboarding.complete && (
        <div className="mb-4 rounded-xl border border-indigo-200 bg-indigo-50/60 p-4 text-sm dark:border-indigo-900 dark:bg-indigo-950/30">
          <div className="mb-2 flex items-center justify-between">
            <span className="font-semibold text-indigo-700 dark:text-indigo-300">
              🪿 Getting started ({onboarding.progress})
            </span>
            <button
              onClick={() => {
                window.localStorage.setItem(`skein-onboarded:${getUser()}`, "1");
                setOnboarding(null);
              }}
              className="text-xs text-zinc-400 underline"
            >
              dismiss
            </button>
          </div>
          <ul className="space-y-1.5">
            {onboarding.steps.map((s) => (
              <li key={s.id}>
                {s.done ? (
                  <span className="text-zinc-400 line-through">✓ {s.label}</span>
                ) : (
                  <>
                    <Link
                      href={s.link}
                      className="font-medium text-zinc-700 underline decoration-dotted hover:text-zinc-900 dark:text-zinc-200"
                    >
                      ○ {s.label}
                    </Link>
                    <span className="ml-1 block pl-4 text-xs text-zinc-500">{s.hint}</span>
                  </>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {b.team.recently_shipped.length > 0 && (
        <div className="mb-4 rounded-xl border border-green-200 bg-green-50 p-4 text-sm font-medium text-green-800 dark:border-green-900 dark:bg-green-950 dark:text-green-200">
          🚢 Shipped:{" "}
          {b.team.recently_shipped.map((e) => e.name).join(" · ")} — recap in
          the knowledge base. Nice work, team.
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <Card title="Needs you">
          {attention.length === 0 ? (
            <p className="text-sm text-zinc-400">{emptyState("allclear")}</p>
          ) : (
            <div className="space-y-3">
              {(Object.keys(GROUP_META) as AttentionItem["group"][])
                .filter((g) => attention.some((a) => a.group === g))
                .map((g) => (
                  <div key={g}>
                    <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-zinc-400">
                      {GROUP_META[g].icon} {GROUP_META[g].title}
                    </p>
                    <ul className="space-y-1.5 text-sm">
                      {attention
                        .filter((a) => a.group === g)
                        .map((a, i) => (
                          <li
                            key={`${a.kind}${a.ref_id}${i}`}
                            className="flex items-start justify-between gap-2"
                          >
                            <span>
                              <Link href={a.link} className="hover:underline">
                                {a.label}
                              </Link>
                              <span
                                className="ml-2 block text-xs text-zinc-400"
                                title="why you're seeing this"
                              >
                                {a.reason}
                              </span>
                            </span>
                            {a.kind === "blocker" && (
                              <button
                                onClick={() => resolveBlocker(a.ref_id)}
                                className="shrink-0 rounded bg-green-100 px-2 py-0.5 text-xs font-medium text-green-700 hover:bg-green-200"
                              >
                                resolve
                              </button>
                            )}
                            {a.kind === "notification" && (
                              <button
                                onClick={async () => {
                                  try {
                                    await api("/api/notifications/read", {
                                      method: "POST",
                                      body: JSON.stringify({ notification_id: a.ref_id }),
                                    });
                                  } catch (e) {
                                    alert(String(e));
                                  }
                                  load();
                                }}
                                className="shrink-0 rounded bg-zinc-200 px-2 py-0.5 text-xs text-zinc-600 hover:bg-zinc-300"
                              >
                                dismiss
                              </button>
                            )}
                          </li>
                        ))}
                    </ul>
                  </div>
                ))}
            </div>
          )}
        </Card>

        <Card title="Your work">
          <ul className="space-y-2 text-sm">
            {b.your_work.tasks.map((t) => (
              <li key={t.id} className="flex items-center justify-between gap-2">
                <span>
                  <span className="text-zinc-400">#{t.id}</span> {t.title}{" "}
                  <span className="text-xs text-zinc-400">
                    [{t.priority}/{t.status}]
                  </span>
                </span>
                <span className="flex shrink-0 gap-1">
                  {t.status === "todo" && (
                    <button
                      onClick={() => patchTask(Number(t.id), "in_progress")}
                      className="rounded bg-zinc-100 px-2 py-0.5 text-xs text-zinc-600 hover:bg-zinc-200 dark:bg-zinc-800"
                    >
                      start
                    </button>
                  )}
                  {t.status !== "done" && (
                    <button
                      onClick={() => patchTask(Number(t.id), "done")}
                      className="rounded bg-green-100 px-2 py-0.5 text-xs font-medium text-green-700 hover:bg-green-200"
                    >
                      done
                    </button>
                  )}
                </span>
              </li>
            ))}
            {b.your_work.tasks.length === 0 && (
              <li className="text-zinc-400">
                No tasks assigned to you — press ⌘K and type &lsquo;todo: …&rsquo;.
              </li>
            )}
            {b.your_work.due_soon.length > 0 && (
              <li className="pt-1 text-xs text-amber-600">
                ⏰ Due within a week:{" "}
                {b.your_work.due_soon.map((t) => `#${t.id} ${t.title}`).join(" · ")}
              </li>
            )}
            <li className="pt-2 text-xs text-zinc-400">
              🌡️ Did Skein reduce coordination effort this week?{" "}
              {(["up", "down"] as const).map((v) => (
                <button
                  key={v}
                  onClick={async () => {
                    try {
                      await api("/api/feedback", {
                        method: "POST",
                        body: JSON.stringify({
                          kind: "pulse",
                          input_text: new Date().toISOString().slice(0, 10),
                          verdict: v,
                        }),
                      });
                      alert("Counted — team tally only, never per person.");
                    } catch (e) {
                      alert(String(e));
                    }
                  }}
                  className="mx-0.5 rounded bg-zinc-100 px-1.5 py-0.5 hover:bg-zinc-200 dark:bg-zinc-800"
                >
                  {v === "up" ? "👍" : "👎"}
                </button>
              ))}
            </li>
            <li className="pt-1 text-xs text-zinc-400">
              <Link href="/settings" className="underline hover:text-zinc-600">
                🌱 set your growth interests (Settings)
              </Link>
            </li>
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
