"use client";

import Link from "next/link";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";

import { actionError, api, getUser, loadError, setUser } from "@/lib/api";
import { reportStatus } from "@/lib/status";
import { StandupComposer } from "@/components/standup-card";
import { GuideHint } from "@/components/guide-hint";
import { emptyState, loadingLine } from "@/lib/whimsy";
import { Card } from "@/components/card";
import { ReceiptLine } from "@/components/receipt";
import { PeekLink } from "@/components/task-peek";
import { PersonInput } from "@/components/person-input";
import { ShortcutText } from "@/components/shortcut";

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
  // it for compatible clients and the /briefing chat command. This page and
  // `skein my-day` render the shared attention projection.
  attention: AttentionItem[];
  // the count the header prints, computed server-side beside the rows so it
  // cannot disagree with the tab title (services/briefing.py)
  attention_total?: number;
  pending_reviews_total?: number;
  // the ISO week `committed_week` is stored against (services/briefing.py)
  this_week?: string;
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

type TodaysThree = {
  team_date: string;
  task_ids: number[];
};

const TODAYS_THREE_EVENT = "skein-todays-three";

function normalizeTodaysThree(
  raw: string,
  teamDate: string,
  available: Set<number>,
): TodaysThree {
  let saved: Partial<TodaysThree> = {};
  try {
    const parsed: unknown = JSON.parse(raw);
    if (parsed && typeof parsed === "object")
      saved = parsed as Partial<TodaysThree>;
  } catch {}

  const taskIds: number[] = [];
  const seen = new Set<number>();
  if (saved.team_date === teamDate && Array.isArray(saved.task_ids)) {
    for (const value of saved.task_ids) {
      if (
        typeof value !== "number" ||
        !Number.isInteger(value) ||
        value <= 0 ||
        seen.has(value) ||
        !available.has(value)
      )
        continue;
      taskIds.push(value);
      seen.add(value);
      if (taskIds.length === 3) break;
    }
  }
  return { team_date: teamDate, task_ids: taskIds };
}

function savedTodaysThreeDate(raw: string): string {
  try {
    const parsed: unknown = JSON.parse(raw);
    if (
      parsed &&
      typeof parsed === "object" &&
      "team_date" in parsed &&
      typeof parsed.team_date === "string" &&
      /^\d{4}-\d{2}-\d{2}$/.test(parsed.team_date)
    )
      return parsed.team_date;
  } catch {}
  return "";
}

// An API key and the two team facts can stay incomplete for a fully active
// browser user. Using onboarding.complete here would hide team context forever.
const GUIDED_CORE_STEPS = new Set(["first_capture", "first_standup"]);

// The /api/onboarding read runs AFTER the briefing (dismissal keys off its
// resolved user), so without this cache every established-but-incomplete
// visitor rendered the collapsed guided layout first and watched the grid
// reflow when the second response landed — on every My Day load, forever.
// Core steps are activity rows and cannot un-happen, so a cached verdict
// only ever goes stale in the safe direction.
const guidedCoreDoneKey = (user: string) => `skein-guided-core-done:${user}`;

// Module scope, not inline: an inline subscribe function is a new identity
// every render, and useSyncExternalStore then resubscribes both listeners on
// each render of the busiest page (settings/page.tsx::subscribeStorage is the
// same pattern for the same reason).
function subscribeTodaysThree(cb: () => void) {
  window.addEventListener("storage", cb);
  window.addEventListener(TODAYS_THREE_EVENT, cb);
  return () => {
    window.removeEventListener("storage", cb);
    window.removeEventListener(TODAYS_THREE_EVENT, cb);
  };
}

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
    <main
      id="content"
      tabIndex={-1}
      className="mx-auto flex min-h-[60vh] w-full max-w-md flex-col justify-center p-6"
    >
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

/** What is open with the outside people attending one meeting.
 *
 *  Lazy: the request fires when the reader asks, not on every My Day load. A
 *  meeting with no outside attendee has no threads, and most do not.
 */
function StakeholderBrief({ eventId }: { eventId: number }) {
  const [open, setOpen] = useState(false);
  // the shape services/stakeholders.py::open_threads returns: one row per
  // party, each carrying its items. The planning cockpit reads the same
  // service and types it this way (app/planning/page.tsx)
  const [threads, setThreads] = useState<
    | { party: string; items: { kind: string; text: string; when: string }[] }[]
    | null
  >(null);
  // a THIRD state. `[]` is what a meeting with no outside attendee returns,
  // so a failure written as `[]` renders "nothing is open" — a claim about the
  // world manufactured from a transport failure, read by somebody walking into
  // the room.
  const [err, setErr] = useState("");

  const show = async () => {
    setOpen(true);
    if (threads !== null) return; // `[]` is a real answer, not a cache miss
    setErr("");
    try {
      const r = await api<{ threads: typeof threads }>(
        `/api/events/${eventId}/stakeholders`,
      );
      setThreads(r.threads ?? []);
      setErr("");
    } catch (e) {
      setErr(actionError(e));
    }
  };

  return (
    // a DIV, not a span: it holds a list, and phrasing content cannot. Its
    // PARENT is a div for the same reason (the events list above).
    <div className="ml-1.5 text-xs text-ink-3">
      {/* the trigger STAYS mounted and toggles. Unmounting it on activation
          dropped a keyboard reader's focus to <body>, and left a failed fetch
          with no control at all — no retry and no way back. Collapsing clears
          nothing, so re-opening after a failure refetches (threads is still
          null). */}
      <button
        onClick={() => (open ? setOpen(false) : show())}
        aria-expanded={open}
        className="rounded bg-raised px-1.5 py-px text-[10px] text-ink-3 hover:bg-line"
      >
        {open ? "hide" : "what is open?"}
      </button>
      {!open ? null : err ? (
        <p className="text-danger">{err}</p>
      ) : threads === null ? (
        <p>Loading…</p>
      ) : threads.length === 0 ? (
        <p>Nothing is open with anyone outside the team in this meeting.</p>
      ) : (
        <ul className="mt-0.5 space-y-0.5">
          {threads.map((t) => (
            <li key={t.party}>
              {t.party}:{" "}
              {t.items
                .map((i) => i.text + (i.when ? ` (${i.when})` : ""))
                .join("; ")}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** What changed since this reader last looked.
 *
 *  Every other surface here is a standing picture: the same rows until
 *  somebody acts, which is correct and is also why a reader skims. This is the
 *  other question, and it answers itself out of the same rows — a health call
 *  that moved, a rule that fired for the first time, a commitment that broke,
 *  an acceptance that arrived (services/delta.py).
 *
 *  Reading it MARKS it. That is the point: the second read is empty, and an
 *  empty second read is what makes the first one worth opening.
 */
function SinceYouLooked() {
  const [d, setD] = useState<{
    since: string;
    quiet: boolean;
    items: {
      kind: string;
      entity: string;
      entity_id: number;
      headline: string;
      direction: string;
      receipts: { message: string; refs: { entity: string; id: number }[] }[];
      link: string;
    }[];
  } | null>(null);
  const [failed, setFailed] = useState(false);
  // Reading and MARKING are two steps, and they must not be one request.
  // React re-invokes an effect on a remount — StrictMode does it on every dev
  // mount — so a fetch that marked as it read consumed the brief with its
  // first call and rendered the empty second answer. The brief is then gone
  // and nobody ever saw it.
  const marked = useRef(false);

  useEffect(() => {
    api<NonNullable<typeof d>>("/api/delta")
      .then(setD)
      .catch(() => setFailed(true));
  }, []);

  // marked once the items are ON SCREEN, never before: a brief that failed to
  // render must still be new tomorrow
  useEffect(() => {
    if (!d?.items?.length || marked.current) return;
    marked.current = true;
    api("/api/delta?mark=true").catch(() => {});
  }, [d]);

  // silent when nothing changed, and silent on failure. This card is ADDITIVE:
  // a reader who sees nothing here has lost nothing, because every row it
  // names is also standing somewhere below it.
  //
  // `items` is tested, not `quiet`: a response missing the array — an older
  // backend behind a newer bundle, a proxy returning an empty body — left
  // `quiet` undefined, so the guard passed and `.map` threw on `undefined`,
  // which unmounts the WHOLE of My Day for a card that is meant to be
  // additive.
  if (failed || !d?.items?.length) return null;

  return (
    <div className="mb-4 rounded-xl border border-thread/30 bg-thread/5 p-4">
      <p className="mb-2 font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-ink-3">
        Since you last looked
      </p>
      <ul className="space-y-1.5 text-sm">
        {d.items.map((i) => (
          <li key={`${i.kind}${i.entity_id}`}>
            <span aria-hidden className="mr-1 text-ink-3">
              {i.direction === "worse"
                ? "▼"
                : i.direction === "better"
                  ? "▲"
                  : "•"}
            </span>
            {/* the glyph is the whole payload for a sighted reader and is
                aria-hidden, so the word carries it for everyone else */}
            <span className="sr-only">
              {i.direction === "worse"
                ? "worse: "
                : i.direction === "better"
                  ? "better: "
                  : ""}
            </span>
            <Link href={i.link} className="hover:underline">
              {i.headline}
            </Link>
            {i.receipts.map((r, n) => (
              <ReceiptLine
                key={n}
                receipt={r}
                className="ml-4 block text-xs text-ink-3"
              />
            ))}
          </li>
        ))}
      </ul>
    </div>
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
  const [onboardingStatus, setOnboardingStatus] = useState<
    "loading" | "ready" | "dismissed" | "failed"
  >("loading");
  const [guidedCoreDone, setGuidedCoreDone] = useState(false);
  const [teamContextOpen, setTeamContextOpen] = useState(false);
  const mainRef = useRef<HTMLElement>(null);
  const markedTodaysThree = useRef(false);
  const todaysThreeKey =
    b?.user && b.user !== "anonymous" ? `skein-todays-three:${b.user}` : "";
  const todaysThreeRaw = useSyncExternalStore(
    subscribeTodaysThree,
    () => {
      if (!todaysThreeKey) return "";
      try {
        return window.localStorage.getItem(todaysThreeKey) ?? "";
      } catch {
        return "";
      }
    },
    () => "",
  );
  const availableTaskIds = new Set(
    (b?.your_work.tasks ?? []).map((task) => Number(task.id)),
  );
  // One payload per resolved user keeps task text and past dates out of storage.
  // The server's team date resets it, and the current rows remove stale ids.
  const todaysThree = normalizeTodaysThree(
    todaysThreeRaw,
    b?.date ?? "",
    availableTaskIds,
  );
  const normalizedTodaysThreeRaw = JSON.stringify(todaysThree);
  const storedTodaysThreeDate = savedTodaysThreeDate(todaysThreeRaw);
  useEffect(() => {
    if (
      !todaysThreeKey ||
      !todaysThreeRaw ||
      storedTodaysThreeDate > (b?.date ?? "") ||
      todaysThreeRaw === normalizedTodaysThreeRaw
    )
      return;
    try {
      window.localStorage.setItem(todaysThreeKey, normalizedTodaysThreeRaw);
      window.dispatchEvent(new Event(TODAYS_THREE_EVENT));
    } catch {}
  }, [
    b?.date,
    normalizedTodaysThreeRaw,
    storedTodaysThreeDate,
    todaysThreeKey,
    todaysThreeRaw,
  ]);
  /** Two setup steps happen on THIS page, not at a route (services/
   *  onboarding.py marks them with a leading "#"). They used to link to
   *  "/", so the first-run reader clicked the most inviting thing on the
   *  page and nothing at all happened. */
  const runStep = (link: string) => {
    if (link === "#capture")
      window.dispatchEvent(new Event("skein-capture-open"));
    if (link === "#standup") document.getElementById("standup-today")?.focus();
  };
  const [error, setError] = useState<string | null>(null);
  // persisted per ISO week — an accidental reload must not re-ask (votes are
  // anonymous server-side, so the client is the only dedupe there is).
  //
  // The SERVER's label, not a second arithmetic. A browser-side ISO week is
  // wrong two ways: it mixes a midnight date with a current-time one, which
  // lands every week of a year whose Jan 1 is a Friday one ahead, and it reads
  // the browser's zone rather than the team day. This key only has to be
  // stable per week, so the falsehood was survivable here and fatal on the
  // commitment chip — which is the reason a second copy must not exist for
  // the next reader to take.
  const pulseWeek = b?.this_week ?? "";
  const pulseVoted = useSyncExternalStore(
    (cb) => {
      window.addEventListener("storage", cb);
      return () => window.removeEventListener("storage", cb);
    },
    () => {
      try {
        return (
          window.localStorage.getItem(`skein-pulse-voted:${getUser()}`) ===
          pulseWeek
        );
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
        if (g !== generation.current) return;
        setB(r); // last request wins
        setError(null); // a past blip must not brick a now-healthy page

        // The bearer can resolve to a different person than the local name.
        // Dismissal belongs to the server-resolved identity, like all page data.
        const onboardKey = `skein-onboarded:${r.user}`;
        // Read the cached core-steps verdict in the same pass, so the layout
        // below never collapses for a user this browser already saw finish.
        setGuidedCoreDone(
          window.localStorage.getItem(guidedCoreDoneKey(r.user)) === "1",
        );
        if (window.localStorage.getItem(onboardKey) === "1") {
          setOnboarding(null);
          setOnboardingStatus("dismissed");
          return;
        }
        api<Onboarding>("/api/onboarding")
          .then((o) => {
            if (g !== generation.current) return;
            // Dismissal can happen while this request is in flight. Re-checking
            // prevents the late response from restoring the card and disclosure.
            if (window.localStorage.getItem(onboardKey) === "1") {
              setOnboarding(null);
              setOnboardingStatus("dismissed");
              return;
            }
            if (o.complete) window.localStorage.setItem(onboardKey, "1");
            if (
              o.steps.every((s) => !GUIDED_CORE_STEPS.has(s.id) || s.done)
            ) {
              window.localStorage.setItem(guidedCoreDoneKey(r.user), "1");
              setGuidedCoreDone(true);
            }
            setOnboarding(o);
            setOnboardingStatus("ready");
          })
          .catch(() => {
            if (g === generation.current) {
              setOnboarding(null);
              setOnboardingStatus("failed");
            }
          });
      })
      .catch((e) => {
        if (g === generation.current) setError(loadError(e));
      });
  }, []);
  useEffect(() => {
    load();
    const refresh = () => load();
    window.addEventListener("skein-attention-change", refresh);
    return () => window.removeEventListener("skein-attention-change", refresh);
  }, [load]);

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

  const toggleTodaysThree = (id: number) => {
    if (storedTodaysThreeDate > (b?.date ?? "")) {
      reportStatus(
        "The team date changed in another tab. Reload My Day, then select tasks again.",
      );
      return;
    }
    const selected = todaysThree.task_ids.includes(id);
    if (!selected && todaysThree.task_ids.length >= 3) {
      reportStatus(
        "Today's Three already has three tasks. Remove one, then add another.",
      );
      return;
    }
    const taskIds = selected
      ? todaysThree.task_ids.filter((taskId) => taskId !== id)
      : [...todaysThree.task_ids, id];
    try {
      window.localStorage.setItem(
        todaysThreeKey,
        JSON.stringify({ team_date: b?.date ?? "", task_ids: taskIds }),
      );
      window.dispatchEvent(new Event(TODAYS_THREE_EVENT));
    } catch {
      reportStatus(
        "Could not save Today's Three in this browser. Check that browser storage is enabled, then try again.",
      );
      return;
    }
    if (!selected && !markedTodaysThree.current) {
      markedTodaysThree.current = true;
      api("/api/field-guide/todays-three", { method: "POST" }).catch(() => {
        markedTodaysThree.current = false;
      });
    }
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

  // Answer where the ask is read. The question row said "someone is waiting
  // on the answer" and shipped the reader to Work → Browse to type it — the
  // same act-where-you-read rule the blocker and meeting rows above already
  // follow. The deep link stays for context; the common case needs no
  // navigation at all.
  const [questionAction, setQuestionAction] = useState<{
    id: number;
    mode: "answer" | "reassign";
  } | null>(null);
  const answerQuestion = async (id: number, answer: string) => {
    try {
      await api(`/api/questions/${id}/answer`, {
        method: "POST",
        body: JSON.stringify({ answer }),
      });
      setQuestionAction(null);
      reportStatus(`Question #${id} answered.`, "confirmation");
    } catch (e) {
      reportStatus(actionError(e));
    }
    load();
  };
  const reassignQuestion = async (id: number, who: string) => {
    try {
      await api(`/api/questions/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ assigned_to: who }),
      });
      setQuestionAction(null);
      reportStatus(`Question #${id} assigned to ${who}.`, "confirmation");
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
      <main
        id="content"
        tabIndex={-1}
        className="mx-auto w-full max-w-5xl xl:max-w-6xl p-4 sm:p-6 text-sm text-danger"
      >
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

  // the label `committed_week` stores (services/weekly.py), taken from the
  // SERVER so the chip and the ordering it explains cannot disagree. Computed
  // in the browser it was wrong twice: the ISO-week arithmetic mixed a
  // midnight date with a current-time one, which put every week of 2027 one
  // ahead, and even correct it would read the browser's timezone rather than
  // the team day the server sorts by. Empty string matches no row, so an
  // older backend simply shows no chip.
  const thisWeek = b.this_week ?? "";
  const todaysThreeTasks = todaysThree.task_ids
    .map((id) => b.your_work.tasks.find((task) => Number(task.id) === id))
    .filter((task): task is Row => Boolean(task));
  const attention = b.attention ?? [];
  // A server that sends no `audience` (an older backend behind a newer
  // bundle) falls back to "you": every row lands in one card, rather than the
  // page silently emptying.
  const yours = attention.filter((a) => (a.audience ?? "you") === "you");
  const teamQueue = attention.filter((a) => a.audience === "team");
  // review items in `attention` are LIMITed to 50; the honest total rides
  // separately so the overflow line below can name what the list cannot show
  const shownReviews = attention.filter((a) => a.group === "review").length;
  const extraReviews = Math.max(
    0,
    (b.pending_reviews_total ?? 0) - shownReviews,
  );
  // the server's number, not a second one computed here: `/api/attention`
  // feeds the tab title from the same rule, and a browser-side count drifted
  // the moment either side capped, coalesced or added a group — the reader saw
  // "(12)" on a tab over a page that said nothing was waiting. The local
  // filter is the fallback for a server that does not send it yet.
  const needsCount =
    b.attention_total ?? yours.filter((a) => a.group !== "notice").length;
  // counted from the rows the card actually renders, so it cannot disagree
  // with what is on screen — the drift rule above is about a second copy of
  // the JUDGMENT count, and this is the other half of the same rows
  const noticeCount = yours.filter((a) => a.group === "notice").length;
  const guidedFirstWeek =
    !guidedCoreDone &&
    (onboardingStatus === "loading" ||
      Boolean(
        onboarding?.steps.some((s) => GUIDED_CORE_STEPS.has(s.id) && !s.done),
      ));
  const GROUP_META: Record<
    AttentionItem["group"],
    { title: string; tone: string }
  > = {
    decide: { title: "Decide", tone: "bg-thread-solid" },
    unblock: { title: "Unblock", tone: "bg-danger" },
    commit: { title: "Promise", tone: "bg-weld" },
    review: { title: "Review", tone: "bg-thread-solid" },
    notice: { title: "Notice", tone: "bg-line-strong" },
  };
  const teamQueueCard =
    teamQueue.length > 0 || extraReviews > 0 ? (
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
    ) : null;
  const teamTodayCard = (
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
            <div className="min-w-0">
              {e.title}
              <StakeholderBrief eventId={Number(e.id)} />
            </div>
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
  );
  const sinceYesterdayCard = (
    <Card title="Since yesterday">
      <ul className="space-y-1">
        {b.team.recent_activity.slice(0, 12).map((a) => (
          <li key={a.id} className="text-xs text-ink-3">
            <span className="font-medium text-ink-2">{a.actor}</span>{" "}
            {String(a.action).replace(/_/g, " ")} {a.detail}
          </li>
        ))}
        {b.team.recent_activity.length === 0 && (
          <li className="text-xs text-ink-3">Quiet so far.</li>
        )}
      </ul>
    </Card>
  );
  const teamContextCount =
    teamQueue.length +
    extraReviews +
    b.team.escalated_blockers.length +
    b.team.todays_events.length +
    Math.min(12, b.team.recent_activity.length);

  return (
    <main
      ref={mainRef}
      id="content"
      tabIndex={-1}
      className="mx-auto flex w-full max-w-5xl flex-col p-4 sm:p-6 xl:max-w-6xl"
    >
      {error && (
        <p className="mb-3 rounded-lg border border-danger/30 bg-danger/10 px-3 py-1.5 text-xs text-danger">
          Last refresh failed. Skein shows the state from the last good load.{" "}
          {error}
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
        {/* the count deliberately excludes notices (briefing.py counts what
            asks for a judgment) — but the card below visibly holds them, so
            a bare "1 thing needs you" over five rows read as a broken count */}
        {noticeCount > 0
          ? ` · ${noticeCount} notice${noticeCount > 1 ? "s" : ""}`
          : ""}
      </p>

      {onboarding && !onboarding.complete && (
        <section
          aria-labelledby="first-week-setup-title"
          className="mb-4 rounded-xl border border-thread-solid/25 bg-card p-4 text-sm shadow-card"
        >
          {(() => {
            // personal steps drive the checklist; team facts are a separate
            // strip — a new teammate is never handed team-level workflows
            const personal = onboarding.steps.filter((s) => s.scope !== "team");
            const teamSteps = onboarding.steps.filter(
              (s) => s.scope === "team",
            );
            const total = personal.length;
            const doneN = personal.filter((s) => s.done).length;
            const pct = total ? (doneN / total) * 100 : 0;
            return (
              <>
                <div className="mb-1 flex items-center justify-between">
                  <h2
                    id="first-week-setup-title"
                    className="font-semibold text-ink"
                  >
                    Your first-week setup{" "}
                    <span className="font-mono text-[11px] font-medium text-ink-3">
                      {doneN}/{total}
                    </span>
                  </h2>
                  <button
                    onClick={() => {
                      window.localStorage.setItem(
                        `skein-onboarded:${b.user}`,
                        "1",
                      );
                      setOnboarding(null);
                      setOnboardingStatus("dismissed");
                      requestAnimationFrame(() => mainRef.current?.focus());
                    }}
                    aria-label="Dismiss first-week setup"
                    className="text-xs text-ink-3 underline"
                    title="Bring it back anytime from Settings"
                  >
                    dismiss
                  </button>
                </div>
                <div className="relative mb-5 mt-4 h-[3px] rounded-full bg-line">
                  <div
                    className="h-full rounded-full motion-safe:transition-[width] motion-safe:duration-500"
                    style={{
                      width: `${pct}%`,
                      background:
                        "linear-gradient(90deg, var(--thread-solid), var(--weld))",
                    }}
                  />
                  {personal.map((s, i) => (
                    <span
                      key={s.id}
                      className={
                        "absolute top-1/2 size-[7px] -translate-x-1/2 -translate-y-1/2 rounded-full " +
                        (s.done
                          ? "bg-thread-solid"
                          : "border border-line-strong bg-card")
                      }
                      style={{
                        left: `${total > 1 ? (i / (total - 1)) * 100 : 0}%`,
                      }}
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
                          <Link
                            href={s.link}
                            className="underline hover:text-ink-2"
                          >
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
        </section>
      )}

      {b.team.recently_shipped.length > 0 && (
        <div className="order-last mb-4 mt-4 rounded-xl border border-ok/30 bg-ok/10 p-4 text-sm font-medium text-ok md:order-none md:mt-0">
          🚢 Shipped: {b.team.recently_shipped.map((e) => e.name).join(" · ")} —
          recap in the knowledge base. Nice work, team.
        </div>
      )}

      <SinceYouLooked />

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
                      <span
                        aria-hidden
                        className={`h-0.5 w-3 rounded-full ${GROUP_META[g].tone}`}
                      />
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
                              {a.kind === "question" && (
                                <span className="ml-2 mt-0.5 block text-xs text-ink-3">
                                  {questionAction?.id === a.ref_id ? (
                                    <span className="flex flex-wrap items-center gap-1.5">
                                      {questionAction.mode === "answer" ? (
                                        <input
                                          autoFocus
                                          name="answer-question"
                                          aria-label={`Answer question #${a.ref_id}`}
                                          placeholder="the answer — Enter to record it"
                                          onKeyDown={(ev) => {
                                            if (ev.key === "Escape")
                                              setQuestionAction(null);
                                            const answer = (
                                              ev.target as HTMLInputElement
                                            ).value.trim();
                                            if (ev.key === "Enter" && answer)
                                              answerQuestion(a.ref_id, answer);
                                          }}
                                          className="w-64 max-w-full rounded-lg border border-line-strong bg-transparent px-2 py-0.5 outline-none focus:border-thread-solid"
                                        />
                                      ) : (
                                        <PersonInput
                                          autoFocus
                                          name="reassign-question"
                                          aria-label={`Assign question #${a.ref_id} to`}
                                          placeholder="teammate's name — Enter to assign"
                                          onKeyDown={(ev) => {
                                            if (ev.key === "Escape")
                                              setQuestionAction(null);
                                            const who = (
                                              ev.target as HTMLInputElement
                                            ).value.trim();
                                            if (ev.key === "Enter" && who)
                                              reassignQuestion(a.ref_id, who);
                                          }}
                                          onChange={(ev) => {
                                            // a mouse-picked datalist suggestion must
                                            // commit too — picks arrive as
                                            // insertReplacementText (or undefined
                                            // inputType in Firefox), typing as
                                            // insertText (app/dashboard/page.tsx)
                                            const t = (
                                              ev.nativeEvent as InputEvent
                                            ).inputType;
                                            if (t && t !== "insertReplacementText")
                                              return;
                                            const who = ev.target.value.trim();
                                            if (who)
                                              reassignQuestion(a.ref_id, who);
                                          }}
                                          className="rounded-lg border border-line-strong bg-transparent px-2 py-0.5 outline-none focus:border-thread-solid"
                                        />
                                      )}
                                      <button
                                        onClick={() => setQuestionAction(null)}
                                        className="hover:text-ink"
                                      >
                                        cancel
                                      </button>
                                    </span>
                                  ) : (
                                    <span className="flex gap-2">
                                      <button
                                        onClick={() =>
                                          setQuestionAction({
                                            id: a.ref_id,
                                            mode: "answer",
                                          })
                                        }
                                        className="underline hover:text-ink-2"
                                      >
                                        answer…
                                      </button>
                                      <button
                                        onClick={() =>
                                          setQuestionAction({
                                            id: a.ref_id,
                                            mode: "reassign",
                                          })
                                        }
                                        className="underline hover:text-ink-2"
                                      >
                                        reassign…
                                      </button>
                                    </span>
                                  )}
                                </span>
                              )}
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
                                  onClick={() =>
                                    recordOutcome(a.ref_id, "recorded")
                                  }
                                  className="rounded bg-ok/15 px-2 py-1.5 md:py-0.5 text-xs font-medium text-ok hover:bg-ok/20"
                                  title="something came out of it — write it up on Capture"
                                >
                                  wrote it up
                                </button>
                                <button
                                  onClick={() =>
                                    recordOutcome(a.ref_id, "none")
                                  }
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

        {!guidedFirstWeek && teamQueueCard}

        <Card title="Your work">
          <div className="mb-3 border-b border-line pb-3">
            <StandupComposer
              suggestion={b.your_work.standup_suggestion ?? ""}
              onPosted={load}
            />
          </div>
          {b.your_work.tasks.length > 0 && (
            <section
              aria-labelledby="todays-three-title"
              className="mb-3 rounded-lg border border-thread/25 bg-thread/5 p-3"
            >
              <div className="flex items-baseline justify-between gap-2">
                <h3 id="todays-three-title" className="font-medium text-ink">
                  Today&apos;s Three
                </h3>
                <span className="font-mono text-[11px] text-ink-3">
                  {todaysThreeTasks.length}/3
                </span>
              </div>
              {todaysThreeTasks.length === 0 ? (
                <p className="mt-1 text-xs text-ink-3">
                  Select up to three tasks for today. The full task order stays
                  below.
                </p>
              ) : (
                <ol className="mt-1 space-y-1 text-sm">
                  {todaysThreeTasks.map((task, index) => (
                    <li key={task.id} className="flex items-baseline gap-1.5">
                      <span className="font-mono text-xs text-ink-3">
                        {index + 1}.
                      </span>
                      <PeekLink taskId={Number(task.id)}>{task.title}</PeekLink>
                    </li>
                  ))}
                </ol>
              )}
            </section>
          )}
          <ul className="space-y-2 text-sm">
            {b.your_work.tasks.map((t) => (
              <li
                key={t.id}
                className="flex items-center justify-between gap-2"
              >
                <span>
                  <PeekLink taskId={Number(t.id)}>
                    <span className="text-ink-3">#{t.id}</span> {t.title}
                  </PeekLink>{" "}
                  <span className="text-xs text-ink-3">
                    [{t.priority}/{t.status}]
                  </span>
                  {/* The list is ordered by commitment before priority
                      (services/briefing.py), and an unexplained order teaches
                      readers to distrust the surface — which is the rule every
                      row in the Needs-you card follows with its reason line.
                      Marked, not re-sorted: the chip IS the explanation. */}
                  {t.committed_week === thisWeek ? (
                    <span className="ml-1 rounded bg-thread/10 px-1.5 py-px text-[10px] text-thread">
                      this week
                    </span>
                  ) : null}
                </span>
                <span className="flex shrink-0 gap-1">
                  {/* No aria-pressed: the name already flips between Add and
                      Remove, and a state-flipping name PLUS a pressed state
                      reads as "Remove … pressed" — announced as removed while
                      the task was just added (ARIA APG, toggle buttons). */}
                  <button
                    type="button"
                    aria-label={`${
                      todaysThree.task_ids.includes(Number(t.id))
                        ? "Remove"
                        : "Add"
                    } task #${t.id} ${
                      todaysThree.task_ids.includes(Number(t.id))
                        ? "from"
                        : "to"
                    } Today's Three`}
                    onClick={() => toggleTodaysThree(Number(t.id))}
                    className="rounded bg-thread/10 px-2 py-1.5 text-xs text-thread hover:bg-thread/15 md:py-0.5"
                  >
                    {todaysThree.task_ids.includes(Number(t.id))
                      ? "remove"
                      : "add"}
                  </button>
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
                No tasks assigned to you. Select Capture in the top bar and type
                &lsquo;todo: …&rsquo;.
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
                            window.localStorage.setItem(
                              `skein-pulse-voted:${getUser()}`,
                              pulseWeek,
                            );
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

        {!guidedFirstWeek && teamTodayCard}

        {/* empty:hidden — GuideHint renders nothing for an anonymous reader or
            once every knot is tried, and a childless grid item still takes a
            row, opening a gap between the cards above and below it */}
        <div className="md:col-span-2 empty:hidden">
          <GuideHint />
        </div>

        {guidedFirstWeek && (
          <div className="md:col-span-2">
            <button
              type="button"
              aria-expanded={teamContextOpen}
              aria-controls="guided-team-context"
              onClick={() => setTeamContextOpen((open) => !open)}
              className="w-full rounded-xl border border-line bg-card px-4 py-3 text-left text-sm font-medium text-ink shadow-card hover:border-line-strong"
            >
              {teamContextOpen ? "Hide" : "Show"} team context (
              {teamContextCount} {teamContextCount === 1 ? "item" : "items"})
            </button>
            {teamContextOpen && (
              <div
                id="guided-team-context"
                className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2"
              >
                {teamQueueCard}
                {teamTodayCard}
                {sinceYesterdayCard}
              </div>
            )}
          </div>
        )}

        {!guidedFirstWeek && sinceYesterdayCard}
      </div>
    </main>
  );
}
