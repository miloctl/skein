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
    <section className="rounded-xl border border-line bg-card p-4 shadow-card">
      <h2 className="mb-3 font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-ink-3">
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

// one wave per session, then stillness; memoized so StrictMode double-renders
// and mid-animation re-renders see the same answer (wave renders client-only,
// behind the briefing-loaded gate, so SSR never sees it)
let wavedThisSession: boolean | undefined;
function shouldWave() {
  if (typeof window === "undefined") return false;
  if (wavedThisSession === undefined) {
    wavedThisSession = !sessionStorage.getItem("skein-waved");
    sessionStorage.setItem("skein-waved", "1");
  }
  return wavedThisSession;
}

export default function MyDay() {
  const [b, setB] = useState<Briefing | null>(null);
  const [onboarding, setOnboarding] = useState<Onboarding | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pulseVoted, setPulseVoted] = useState(false);
  const waveOnce = shouldWave();

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
      // the loom advances one repeat — Ship It is the selvage's only trigger
      const selvage = document.getElementById("selvage");
      if (selvage) {
        selvage.classList.add("selvage-celebrate");
        setTimeout(() => selvage.classList.remove("selvage-celebrate"), 700);
      }
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
      <main className="mx-auto max-w-3xl p-8 text-sm text-danger">
        Could not reach the backend — is it running? ({error})
      </main>
    );
  if (!b) return <main className="p-8 text-sm text-ink-3">{loadingLine()}</main>;

  const attention = b.attention ?? [];
  const needsCount = attention.filter((a) => a.group !== "notice").length;
  const GROUP_META: Record<AttentionItem["group"], { title: string; tone: string }> = {
    decide: { title: "Decide", tone: "bg-thread-solid" },
    unblock: { title: "Unblock", tone: "bg-danger" },
    commit: { title: "Commit", tone: "bg-weld" },
    review: { title: "Review", tone: "bg-thread-solid" },
    notice: { title: "Notice", tone: "bg-line-strong" },
  };

  return (
    <main className="mx-auto w-full max-w-5xl p-6">
      <h1 className="mb-1 font-display text-[28px]/[1.15] font-semibold tracking-[-0.01em] text-ink">
        Good day, {b.user === "anonymous" ? "there" : b.user}{" "}
        <span className={waveOnce ? "wave-once" : ""}>👋</span>
      </h1>
      <p className="mb-6 text-sm text-ink-3">
        {b.date} ·{" "}
        {needsCount === 0
          ? "nothing is waiting on you"
          : `${needsCount} thing${needsCount > 1 ? "s" : ""} need${needsCount > 1 ? "" : "s"} you`}
        {b.user === "anonymous" ? " · set your name (top right) to personalize" : ""}
      </p>

      {onboarding && !onboarding.complete && (
        <div className="mb-4 rounded-xl border border-thread-solid/25 bg-card p-4 text-sm shadow-card">
          <div className="mb-1 flex items-center justify-between">
            <span className="font-semibold text-ink">
              Weaving your first week{" "}
              <span className="font-mono text-[11px] font-medium text-ink-3">
                {onboarding.progress}
              </span>
            </span>
            <button
              onClick={() => {
                window.localStorage.setItem(`skein-onboarded:${getUser()}`, "1");
                setOnboarding(null);
              }}
              className="text-xs text-ink-3 underline"
              title="Bring it back anytime from Settings"
            >
              dismiss
            </button>
          </div>
          {(() => {
            const total = onboarding.steps.length;
            const doneN = onboarding.steps.filter((s) => s.done).length;
            const pct = total ? (doneN / total) * 100 : 0;
            return (
              <div className="relative mb-5 mt-4 h-[3px] rounded-full bg-line">
                <div
                  className="h-full rounded-full transition-[width] duration-500"
                  style={{
                    width: `${pct}%`,
                    background: "linear-gradient(90deg, var(--thread-solid), var(--weld))",
                  }}
                />
                {onboarding.steps.map((s, i) => (
                  <span
                    key={s.id}
                    className={
                      "absolute top-1/2 size-[7px] -translate-x-1/2 -translate-y-1/2 rounded-full " +
                      (s.done ? "bg-thread-solid" : "border border-line-strong bg-card")
                    }
                    style={{ left: `${total > 1 ? (i / (total - 1)) * 100 : 0}%` }}
                  />
                ))}
                <span
                  className="absolute -top-[20px] -translate-x-1/2 text-sm motion-safe:transition-[left] motion-safe:duration-500"
                  style={{ left: `${Math.min(pct, 97)}%` }}
                  aria-hidden
                >
                  <span className="goose">🪿</span>
                </span>
              </div>
            );
          })()}
          <ul className="space-y-1.5">
            {onboarding.steps.map((s) => (
              <li key={s.id}>
                {s.done ? (
                  <span className="text-ink-3">✓ {s.label}</span>
                ) : (
                  <>
                    <Link
                      href={s.link}
                      className="font-medium text-ink underline decoration-dotted decoration-line-strong underline-offset-2 hover:text-thread"
                    >
                      ○ {s.label}
                    </Link>
                    <span className="ml-1 block pl-4 text-xs text-ink-3">{s.hint}</span>
                  </>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {b.team.recently_shipped.length > 0 && (
        <div className="mb-4 rounded-xl border border-ok/30 bg-ok/10 p-4 text-sm font-medium text-ok">
          🚢 Shipped:{" "}
          {b.team.recently_shipped.map((e) => e.name).join(" · ")} — recap in
          the knowledge base. Nice work, team.
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <Card title="Needs you">
          {attention.length === 0 ? (
            <p className="text-sm text-ink-3">{emptyState("allclear")}</p>
          ) : (
            <div className="space-y-3">
              {(Object.keys(GROUP_META) as AttentionItem["group"][])
                .filter((g) => attention.some((a) => a.group === g))
                .map((g) => (
                  <div key={g}>
                    <p className="mb-1 flex items-center gap-1.5 font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-ink-3">
                      <span aria-hidden className={`h-0.5 w-3 rounded-full ${GROUP_META[g].tone}`} />
                      {GROUP_META[g].title}
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
                                {/* notifications may carry markdown bold; this list is plain text */}
                                {a.label.replaceAll("**", "")}
                              </Link>
                              <span
                                className="ml-2 block text-xs text-ink-3"
                                title="why you're seeing this"
                              >
                                {a.reason}
                              </span>
                            </span>
                            {a.kind === "blocker" && (
                              <button
                                onClick={() => resolveBlocker(a.ref_id)}
                                className="shrink-0 rounded bg-ok/15 px-2 py-0.5 text-xs font-medium text-ok hover:bg-ok/20"
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
                                className="shrink-0 rounded bg-raised px-2 py-0.5 text-xs text-ink-2 hover:bg-line"
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
                  <span className="text-ink-3">#{t.id}</span> {t.title}{" "}
                  <span className="text-xs text-ink-3">
                    [{t.priority}/{t.status}]
                  </span>
                </span>
                <span className="flex shrink-0 gap-1">
                  {t.status === "todo" && (
                    <button
                      onClick={() => patchTask(Number(t.id), "in_progress")}
                      className="rounded bg-raised px-2 py-0.5 text-xs text-ink-2 hover:bg-line"
                    >
                      start
                    </button>
                  )}
                  {t.status !== "done" && (
                    <button
                      onClick={() => patchTask(Number(t.id), "done")}
                      className="rounded bg-ok/15 px-2 py-0.5 text-xs font-medium text-ok hover:bg-ok/20"
                    >
                      done
                    </button>
                  )}
                </span>
              </li>
            ))}
            {b.your_work.tasks.length === 0 && (
              <li className="text-ink-3">
                No tasks assigned to you — press ⌘K and type &lsquo;todo: …&rsquo;.
              </li>
            )}
            {b.your_work.due_soon.length > 0 && (
              <li className="pt-1 text-xs text-weld">
                Due within a week:{" "}
                {b.your_work.due_soon.map((t) => `#${t.id} ${t.title}`).join(" · ")}
              </li>
            )}
            <li className="pt-2 text-xs text-ink-3">
              {pulseVoted ? (
                // identical quiet acknowledgment for both votes — a 👎 must
                // never trigger anything peppy, and no modal ever
                <span>Counted. Team tally only.</span>
              ) : (
                <>
                  Did Skein reduce coordination effort this week?{" "}
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
                          setPulseVoted(true);
                        } catch (e) {
                          alert(String(e));
                        }
                      }}
                      className="mx-0.5 rounded bg-raised px-1.5 py-0.5 hover:bg-line"
                    >
                      {v === "up" ? "👍" : "👎"}
                    </button>
                  ))}
                </>
              )}
            </li>
            <li className="pt-1 text-xs text-ink-3">
              <Link href="/settings" className="underline hover:text-ink-2">
                Set your growth interests (Settings)
              </Link>
            </li>
          </ul>
        </Card>

        <Card title="Team pulse">
          <ul className="space-y-2 text-sm">
            {b.team.escalated_blockers.map((e) => (
              <li key={e.id} className="flex items-center gap-2 text-danger">
                <span
                  aria-hidden
                  className="knot-escalated size-2 shrink-0 rounded-full bg-danger"
                />
                <span>
                  Escalated: #{e.id} {e.title} (owner: {e.owner || "unowned"})
                </span>
              </li>
            ))}
            {b.team.todays_events.map((e) => (
              <li key={e.id} className="flex items-baseline gap-2">
                <span className="font-mono text-xs text-ink-3">
                  {String(e.starts_at).slice(11, 16)}
                </span>
                <span>{e.title}</span>
              </li>
            ))}
            {b.team.escalated_blockers.length === 0 &&
              b.team.todays_events.length === 0 && (
                <li>
                  <div className="loom-idle mb-2" aria-hidden />
                  <p className="text-xs text-ink-3">
                    All threads even — no escalations, nothing scheduled today.
                  </p>
                </li>
              )}
          </ul>
        </Card>

        <Card title="Since yesterday">
          <ul className="space-y-1">
            {b.team.recent_activity.slice(0, 12).map((a) => (
              <li key={a.id} className="text-xs text-ink-3">
                <span className="font-medium text-ink-2">
                  {a.actor}
                </span>{" "}
                {String(a.action).replace(/_/g, " ")} {a.detail}
              </li>
            ))}
            {b.team.recent_activity.length === 0 && (
              <li className="text-xs text-ink-3">Quiet so far.</li>
            )}
          </ul>
        </Card>
      </div>
    </main>
  );
}
