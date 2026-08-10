"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";

import { actionError, api, getUser, loadError, setUser } from "@/lib/api";
import { reportStatus } from "@/lib/status";
import { StandupComposer } from "@/components/standup-card";
import { GuideHint } from "@/components/guide-hint";
import { emptyState, loadingLine } from "@/lib/whimsy";
import { Card } from "@/components/card";
import { PeekLink } from "@/components/task-peek";
import { Shortcut, ShortcutText } from "@/components/shortcut";

type Row = Record<string, string | number | null>;

type AttentionItem = {
  kind: string;
  ref_id: number;
  group: "decide" | "unblock" | "commit" | "review" | "notice";
  // "you" for a row addressed to this reader by name, "team" for a shared
  // queue anyone may work (services/briefing.py). The two render in separate
  // cards: a "Needs you" heading over the team's intake queue and every
  // pending proposal taught readers to discount the card, and this card is
  // the product's daily habit.
  audience?: "you" | "team";
  label: string;
  reason: string;
  link: string;
};

type Briefing = {
  user: string;
  date: string;
  // `needs_you` (the same five lists, uncategorized) is deliberately absent:
  // `attention` IS those rows, already grouped by the judgment each one asks
  // for and carrying its own reason line, and rendering both would ask the
  // reader to notice they are the same work twice. The payload still carries
  // it for `skein my-day` and the /briefing chat command, which have no
  // grouped renderer — see services/briefing.py.
  attention: AttentionItem[];
  // the count the header prints, computed server-side beside the rows so it
  // cannot disagree with the tab title (services/briefing.py)
  attention_total?: number;
  pending_reviews_total?: number;
  your_work: { tasks: Row[]; due_soon: Row[]; standup_suggestion?: string };
  team: {
    recently_shipped: Row[];
    escalated_blockers: Row[];
    todays_events: Row[];
    recent_activity: Row[];
  };
};


type Onboarding = {
  steps: {
    id: string;
    label: string;
    done: boolean;
    link: string;
    hint: string;
    scope: "you" | "team";
  }[];
  complete: boolean;
  progress: string;
};

/** Identity is the concept everything else hangs off — until a name is
 *  picked, the rest of My Day is noise. One question, then the real page. */
function WhoAreYou() {
  const [people, setPeople] = useState<{ name: string; kind: string }[]>([]);
  useEffect(() => {
    api<{ name: string; kind: string }[]>("/api/users")
      .then((u) => setPeople(u.filter((x) => x.kind !== "agent")))
      .catch(() => {});
  }, []);
  const pick = (name: string) => {
    setUser(name);
    window.location.reload();
  };
  return (
    <main id="content" tabIndex={-1} className="mx-auto flex min-h-[60vh] w-full max-w-md flex-col justify-center p-6">
      <h1 className="mb-1 font-display text-[24px]/[1.15] font-semibold tracking-[-0.01em] text-ink">
        Who are you?
      </h1>
      <p className="mb-5 text-sm text-ink-3">
        Everything you do here is recorded under your name — pick it once and
        this browser remembers.
      </p>
      <input
        autoFocus
        name="pick-name"
        placeholder="Your name — Enter to continue"
        aria-label="Your name"
        className="mb-3 w-full rounded-xl border border-line-strong bg-transparent px-3 py-2 text-sm outline-none focus:border-thread-solid"
        onKeyDown={(e) => {
          const name = (e.target as HTMLInputElement).value.trim();
          if (e.key === "Enter" && name) pick(name);
        }}
      />
      {people.length > 0 && (
        <div className="flex flex-wrap gap-2">
          <span className="py-1 text-xs text-ink-3">Already on the team:</span>
          {people.map((p) => (
            <button
              key={p.name}
              onClick={() => pick(p.name)}
              className="rounded-full bg-raised px-3 py-1 text-sm text-ink-2 hover:bg-line hover:text-ink"
            >
              {p.name}
            </button>
          ))}
        </div>
      )}
      <p className="mt-5 text-xs text-ink-3">
        You can also{" "}
        <Link href="/dashboard" className="underline hover:text-ink-2">
          browse the team&apos;s work
        </Link>{" "}
        without picking a name — nothing is attributed to you until you do.
      </p>
    </main>
  );
}

/** The dismiss control, shared by both attention cards.
 *
 *  A personal notification renders under "Needs you" and a team one under
 *  "Team queues" (services/briefing.py sets the audience). `POST
 *  /api/notifications/read` has no other caller in the product — not the CLI,
 *  not chat — so a card that omits this button makes its rows permanent.
 */
function Dismiss({ id, onDone }: { id: number; onDone: () => void }) {
  return (
    <button
      onClick={async () => {
        try {
          await api("/api/notifications/read", {
            method: "POST",
            body: JSON.stringify({ notification_id: id }),
          });
        } catch (e) {
          reportStatus(actionError(e));
        }
        onDone();
      }}
      className="shrink-0 rounded bg-raised px-2 py-1.5 md:py-0.5 text-xs text-ink-2 hover:bg-line"
    >
      dismiss
    </button>
  );
}

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
  /** Two setup steps happen on THIS page, not at a route (services/
   *  onboarding.py marks them with a leading "#"). They used to link to
   *  "/", so the first-run reader clicked the most inviting thing on the
   *  page and nothing at all happened. */
  const runStep = (link: string) => {
    if (link === "#capture") window.dispatchEvent(new Event("skein-capture-open"));
    if (link === "#standup") document.getElementById("standup-today")?.focus();
  };
  const [error, setError] = useState<string | null>(null);
  // persisted per ISO week — an accidental reload must not re-ask (votes are
  // anonymous server-side, so the client is the only dedupe there is)
  const pulseWeek = (() => {
    const d = new Date();
    const day = (d.getDay() + 6) % 7;
    const thu = new Date(d);
    thu.setDate(d.getDate() - day + 3);
    const jan1 = new Date(thu.getFullYear(), 0, 1);
    const week = Math.ceil(((+thu - +jan1) / 86400000 + 1) / 7);
    return `${thu.getFullYear()}-W${week}`;
  })();
  const pulseVoted = useSyncExternalStore(
    (cb) => {
      window.addEventListener("storage", cb);
      return () => window.removeEventListener("storage", cb);
    },
    () => {
      try {
        return window.localStorage.getItem(`skein-pulse-voted:${getUser()}`) === pulseWeek;
      } catch {
        return false;
      }
    },
    () => false,
  );
  const generation = useRef(0);
  const load = useCallback(() => {
    const g = ++generation.current;
    api<Briefing>("/api/briefing")
      .then((r) => {
        if (g === generation.current) {
          setB(r); // last request wins
          setError(null); // a past blip must not brick a now-healthy page
        }
      })
      .catch((e) => {
        if (g === generation.current) setError(loadError(e));
      });
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
      stored = JSON.parse(
        window.localStorage.getItem(`skein-confetti:${getUser()}`) ?? "[]",
      );
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
    window.localStorage.setItem(
      `skein-confetti:${getUser()}`,
      JSON.stringify([...seen].slice(-100)),
    );
  }, [b]);

  const patchTask = async (id: number, status: string) => {
    try {
      await api(`/api/tasks/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ status }),
      });
    } catch (e) {
      reportStatus(actionError(e));
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
      reportStatus(actionError(e));
    }
    load();
  };

  // The daily ask has to be answerable where it is asked. Without these the
  // only way to clear a meeting notice was a POST by hand, so the same
  // meeting came back every morning forever.
  const recordOutcome = async (id: number, outcome: "recorded" | "none") => {
    try {
      await api(`/api/events/${id}/outcome`, {
        method: "POST",
        body: JSON.stringify({ outcome }),
      });
    } catch (e) {
      reportStatus(actionError(e));
    }
    load();
  };

  if (error && !b)
    return (
      <main id="content" tabIndex={-1} className="mx-auto w-full max-w-5xl xl:max-w-6xl p-4 sm:p-6 text-sm text-danger">
        {error}
      </main>
    );
  if (!b)
    return (
      <main
        id="content"
        tabIndex={-1}
        className="mx-auto w-full max-w-5xl p-4 sm:p-6 xl:max-w-6xl"
      >
        <h1 className="mb-1 font-display text-[24px]/[1.15] font-semibold tracking-[-0.01em] text-ink">
          My Day
        </h1>
        <p className="mb-6 max-w-3xl text-sm text-ink-3">{loadingLine()}</p>
      </main>
    );
  if (b.user === "anonymous") return <WhoAreYou />;

  const attention = b.attention ?? [];
  // A server that sends no `audience` (an older backend behind a newer
  // bundle) falls back to "you": every row lands in one card, rather than the
  // page silently emptying.
  const yours = attention.filter((a) => (a.audience ?? "you") === "you");
  const teamQueue = attention.filter((a) => a.audience === "team");
  // review items in `attention` are LIMITed to 50; the honest total rides
  // separately so the overflow line below can name what the list cannot show
  const shownReviews = attention.filter((a) => a.group === "review").length;
  const extraReviews = Math.max(0, (b.pending_reviews_total ?? 0) - shownReviews);
  // the server's number, not a second one computed here: `/api/attention`
  // feeds the tab title from the same rule, and a browser-side count drifted
  // the moment either side capped, coalesced or added a group — the reader saw
  // "(12)" on a tab over a page that said nothing was waiting. The local
  // filter is the fallback for a server that does not send it yet.
  const needsCount =
    b.attention_total ?? yours.filter((a) => a.group !== "notice").length;
  const GROUP_META: Record<AttentionItem["group"], { title: string; tone: string }> = {
    decide: { title: "Decide", tone: "bg-thread-solid" },
    unblock: { title: "Unblock", tone: "bg-danger" },
    commit: { title: "Promise", tone: "bg-weld" },
    review: { title: "Review", tone: "bg-thread-solid" },
    notice: { title: "Notice", tone: "bg-line-strong" },
  };

  return (
    <main
      id="content"
      tabIndex={-1}
      className="mx-auto flex w-full max-w-5xl flex-col p-4 sm:p-6 xl:max-w-6xl"
    >
      {error && (
        <p className="mb-3 rounded-lg border border-danger/30 bg-danger/10 px-3 py-1.5 text-xs text-danger">
          Last refresh failed ({error}) — showing the previous state.
        </p>
      )}
      <h1 className="mb-1 font-display text-[24px]/[1.15] font-semibold tracking-[-0.01em] text-ink">
        Good day, {b.user}{" "}
        {/* called HERE, not in the component body: shouldWave burns the
            once-per-session wave on first call, and above the early returns
            a page that never loads still spent it */}
        <span className={shouldWave() ? "wave-once" : ""}>👋</span>
      </h1>
      <p className="mb-6 max-w-3xl text-sm text-ink-3">
        {b.date} ·{" "}
        {needsCount === 0
          ? "nothing is waiting on you"
          : `${needsCount} thing${needsCount > 1 ? "s" : ""} need${needsCount > 1 ? "" : "s"} you`}
      </p>

      {onboarding && !onboarding.complete && (
        <div className="order-last mb-4 mt-4 rounded-xl border border-thread-solid/25 bg-card p-4 text-sm shadow-card md:order-none md:mt-0">
          {(() => {
            // personal steps drive the checklist; team facts are a separate
            // strip — a new teammate is never handed team-level workflows
            const personal = onboarding.steps.filter((s) => s.scope !== "team");
            const teamSteps = onboarding.steps.filter((s) => s.scope === "team");
            const total = personal.length;
            const doneN = personal.filter((s) => s.done).length;
            const pct = total ? (doneN / total) * 100 : 0;
            return (
              <>
                <div className="mb-1 flex items-center justify-between">
                  <span className="font-semibold text-ink">
                    Your first-week setup{" "}
                    <span className="font-mono text-[11px] font-medium text-ink-3">
                      {doneN}/{total}
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
                <div className="relative mb-5 mt-4 h-[3px] rounded-full bg-line">
                  <div
                    className="h-full rounded-full transition-[width] duration-500"
                    style={{
                      width: `${pct}%`,
                      background: "linear-gradient(90deg, var(--thread-solid), var(--weld))",
                    }}
                  />
                  {personal.map((s, i) => (
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
                <ul className="space-y-1.5">
                  {personal.map((s) => (
                    <li key={s.id}>
                      {s.done ? (
                        <span className="text-ink-3">✓ {s.label}</span>
                      ) : (
                        <>
                          {s.link.startsWith("#") ? (
                            <button
                              onClick={() => runStep(s.link)}
                              className="font-medium text-ink underline decoration-dotted decoration-line-strong underline-offset-2 hover:text-thread"
                            >
                              ○ {s.label}
                            </button>
                          ) : (
                            <Link
                              href={s.link}
                              className="font-medium text-ink underline decoration-dotted decoration-line-strong underline-offset-2 hover:text-thread"
                            >
                              ○ {s.label}
                            </Link>
                          )}
                          <span className="ml-1 block pl-4 text-xs text-ink-3">
                            {/* onboarding.py writes the ⌘K token — this is
                                the step that teaches capture, so the key it
                                names has to be the reader's */}
                            <ShortcutText text={s.hint} />
                          </span>
                        </>
                      )}
                    </li>
                  ))}
                </ul>
                {teamSteps.some((s) => !s.done) && (
                  <p className="mt-3 border-t border-line pt-2 text-xs text-ink-3">
                    Team setup (anyone can do these):{" "}
                    {teamSteps.map((s, i) => (
                      <span key={s.id}>
                        {i > 0 && " · "}
                        {s.done ? (
                          <span>✓ {s.label}</span>
                        ) : (
                          <Link href={s.link} className="underline hover:text-ink-2">
                            ○ {s.label}
                          </Link>
                        )}
                      </span>
                    ))}
                  </p>
                )}
              </>
            );
          })()}
        </div>
      )}

      {b.team.recently_shipped.length > 0 && (
        <div className="order-last mb-4 mt-4 rounded-xl border border-ok/30 bg-ok/10 p-4 text-sm font-medium text-ok md:order-none md:mt-0">
          🚢 Shipped:{" "}
          {b.team.recently_shipped.map((e) => e.name).join(" · ")} — recap in
          the knowledge base. Nice work, team.
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <Card title="Needs you">
          {yours.length === 0 ? (
            <p className="text-sm text-ink-3">{emptyState("allclear")}</p>
          ) : (
            <div className="space-y-3">
              {(Object.keys(GROUP_META) as AttentionItem["group"][])
                .filter((g) => yours.some((a) => a.group === g))
                .map((g) => (
                  <div key={g}>
                    <p className="mb-1 flex items-center gap-1.5 font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-ink-3">
                      <span aria-hidden className={`h-0.5 w-3 rounded-full ${GROUP_META[g].tone}`} />
                      {GROUP_META[g].title}
                    </p>
                    <ul className="space-y-1.5 text-sm">
                      {yours
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
                                title="why you see this"
                              >
                                {a.reason}
                              </span>
                            </span>
                            {a.kind === "blocker" && (
                              <button
                                onClick={() => resolveBlocker(a.ref_id)}
                                className="shrink-0 rounded bg-ok/15 px-2 py-1.5 md:py-0.5 text-xs font-medium text-ok hover:bg-ok/20"
                              >
                                resolve
                              </button>
                            )}
                            {a.kind === "meeting" && (
                              <span className="flex shrink-0 gap-1">
                                <button
                                  onClick={() => recordOutcome(a.ref_id, "recorded")}
                                  className="rounded bg-ok/15 px-2 py-1.5 md:py-0.5 text-xs font-medium text-ok hover:bg-ok/20"
                                  title="something came out of it — write it up on Capture"
                                >
                                  wrote it up
                                </button>
                                <button
                                  onClick={() => recordOutcome(a.ref_id, "none")}
                                  className="rounded bg-ink-3/15 px-2 py-1.5 md:py-0.5 text-xs font-medium text-ink-2 hover:bg-ink-3/20"
                                  title="nothing came out of it — this is what the weekly finding counts"
                                >
                                  nothing
                                </button>
                              </span>
                            )}
                            {a.kind === "notification" && (
                              <Dismiss id={a.ref_id} onDone={load} />
                            )}
                          </li>
                        ))}
                    </ul>
                  </div>
                ))}
            </div>
          )}
        </Card>

        {/* The shared queues, named as shared. These rows are real work and
            they belong on My Day — but nobody assigned them to this reader,
            and putting them under "Needs you" was what taught people that the
            heading does not mean what it says. */}
        {/* rendered for the overflow line alone when the queue itself is
            empty: every pending proposal can be one this reader asked for, and
            the remainder past the 50-row cap has nowhere else to be said */}
        {(teamQueue.length > 0 || extraReviews > 0) && (
          <Card title="Team queues">
            <p className="mb-2 text-xs text-ink-3">
              Open to anyone on the team. Nobody assigned these to you.
            </p>
            <ul className="space-y-1.5 text-sm">
              {teamQueue.map((a, i) => (
                <li
                  key={`${a.kind}${a.ref_id}${i}`}
                  className="flex items-start justify-between gap-2"
                >
                  <span>
                    <Link href={a.link} className="hover:underline">
                      {a.label.replaceAll("**", "")}
                    </Link>
                    <span className="ml-2 block text-xs text-ink-3">
                      {a.reason}
                    </span>
                  </span>
                  {/* a team announcement is delivered to this card, and
                      `/api/notifications/read` has no other caller anywhere in
                      the product — without this control the row is permanent */}
                  {a.kind === "notification" && (
                    <Dismiss id={a.ref_id} onDone={load} />
                  )}
                </li>
              ))}
              {extraReviews > 0 && (
                <li className="text-xs text-ink-3">
                  {extraReviews} more proposal
                  {extraReviews === 1 ? " waits" : "s wait"} in{" "}
                  <Link href="/review" className="underline">
                    Inbox → Approvals
                  </Link>
                  .
                </li>
              )}
            </ul>
          </Card>
        )}

        <Card title="Your work">
          <div className="mb-3 border-b border-line pb-3">
            <StandupComposer
              suggestion={b.your_work.standup_suggestion ?? ""}
              onPosted={load}
            />
          </div>
          <ul className="space-y-2 text-sm">
            {b.your_work.tasks.map((t) => (
              <li key={t.id} className="flex items-center justify-between gap-2">
                <span>
                  <PeekLink taskId={Number(t.id)}>
                    <span className="text-ink-3">#{t.id}</span> {t.title}
                  </PeekLink>{" "}
                  <span className="text-xs text-ink-3">
                    [{t.priority}/{t.status}]
                  </span>
                </span>
                <span className="flex shrink-0 gap-1">
                  {t.status === "todo" && (
                    <button
                      onClick={() => patchTask(Number(t.id), "in_progress")}
                      className="rounded bg-raised px-2 py-1.5 md:py-0.5 text-xs text-ink-2 hover:bg-line"
                    >
                      start
                    </button>
                  )}
                  {t.status !== "done" && (
                    <button
                      onClick={() => patchTask(Number(t.id), "done")}
                      className="rounded bg-ok/15 px-2 py-1.5 md:py-0.5 text-xs font-medium text-ok hover:bg-ok/20"
                    >
                      done
                    </button>
                  )}
                </span>
              </li>
            ))}
            {b.your_work.tasks.length === 0 && (
              <li className="text-ink-3">
                No tasks assigned to you — use quick capture
                <span className="[@media(any-pointer:coarse)]:hidden">
                  {" ("}
                  <Shortcut />
                  {")"}
                </span>{" "}
                and type &lsquo;todo: …&rsquo;.
              </li>
            )}
            {b.your_work.due_soon.length > 0 && (
              <li className="pt-1 text-xs text-weld">
                Due within a week:{" "}
                {/* links, not a joined string: this line names the exact row
                    and used to be the dead end the task peek was built for */}
                {b.your_work.due_soon.map((t, i) => (
                  <span key={t.id}>
                    {i > 0 ? " · " : ""}
                    <PeekLink taskId={Number(t.id)}>
                      #{t.id} {t.title}
                    </PeekLink>
                  </span>
                ))}
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
                          try {
                            window.localStorage.setItem(`skein-pulse-voted:${getUser()}`, pulseWeek);
                          } catch {}
                          window.dispatchEvent(new Event("storage"));
                        } catch (e) {
                          reportStatus(actionError(e));
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

        <Card title="Team today">
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

      <GuideHint />
    </main>
  );
}
