"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { FirstWatchActions } from "@/components/first-watch-actions";
import { ShortcutText } from "@/components/shortcut";
import { openTaskPeek } from "@/components/task-peek";
import { actionError, api } from "@/lib/api";
import {
  BOSUN_PROMPT,
  consumeFirstWatchQuery,
  FIRST_WATCH_EVENT,
  FIRST_WATCH_STEP_IDS,
  firstWatchKey,
  firstWatchStepNumber,
  freshFirstWatch,
  HELP_PROMPT,
  parseFirstWatch,
  type FirstWatchCard,
  type FirstWatchRun,
  type FirstWatchStepId,
} from "@/lib/first-watch";
import { reportStatus } from "@/lib/status";

export function FirstWatch() {
  const pathname = usePathname();
  const [identity, setIdentity] = useState<string | null>(null);
  const [cards, setCards] = useState<FirstWatchCard[] | null>(null);
  const [run, setRun] = useState<FirstWatchRun | null>(null);
  const [loading, setLoading] = useState(false);
  const [stepMessage, setStepMessage] = useState("");
  const [peekResult, setPeekResult] = useState<"" | "loaded" | "unavailable">("");
  const [searchDone, setSearchDone] = useState(false);
  const [composeText, setComposeText] = useState<string | null>(null);
  const [chatState, setChatState] = useState<
    "loading" | "live" | "deterministic" | "unavailable" | "unknown"
  >("loading");
  const [announcement, setAnnouncement] = useState("");
  const headingRef = useRef<HTMLHeadingElement>(null);
  const resumeRef = useRef<HTMLButtonElement>(null);
  const generation = useRef(0);
  const identityRef = useRef<string | null>(null);
  const runRef = useRef<FirstWatchRun | null>(null);
  const evidenceGeneration = useRef(0);
  const pendingPeek = useRef<{
    generation: number;
    taskId: number;
    source: "task_peek" | "search";
  } | null>(null);
  const searchCompletionPending = useRef(false);
  const expectedCompose = useRef<{
    generation: number;
    person: string;
    text: string;
  } | null>(null);
  const focusAfterRoute = useRef<string | null>(null);
  const focusHeading = useRef(false);
  const focusResume = useRef(false);
  const markAfterRender = useRef(false);

  const resetEvidence = useCallback(() => {
    evidenceGeneration.current += 1;
    pendingPeek.current = null;
    searchCompletionPending.current = false;
    expectedCompose.current = null;
    setStepMessage("");
    setPeekResult("");
    setSearchDone(false);
    setComposeText(null);
    setChatState("loading");
    setAnnouncement("");
  }, []);

  const resolve = useCallback(async (requested: boolean) => {
    const current = ++generation.current;
    resetEvidence();
    if (requested) setAnnouncement("Loading First Watch…");
    identityRef.current = null;
    runRef.current = null;
    setIdentity(null);
    setCards(null);
    setRun(null);
    setLoading(requested);
    try {
      const who = await api<{ user: string }>("/api/whoami");
      if (current !== generation.current) return;
      setIdentity(who.user);
      if (!who.user || who.user === "anonymous") {
        setAnnouncement("");
        setLoading(false);
        return;
      }

      let stored: FirstWatchRun | null = null;
      try {
        stored = parseFirstWatch(window.localStorage.getItem(firstWatchKey(who.user)));
      } catch {
        if (requested)
          reportStatus(
            "First Watch did not read saved progress in this browser. Start it again to continue.",
          );
      }
      if (!requested && !stored) {
        setLoading(false);
        return;
      }

      const response = await api<{ steps: FirstWatchCard[] }>("/api/field-guide/first-watch");
      if (current !== generation.current) return;
      if (
        response.steps.length !== FIRST_WATCH_STEP_IDS.length ||
        response.steps.some((card, index) => card.id !== FIRST_WATCH_STEP_IDS[index])
      )
        throw new Error("First Watch content does not match this version of Skein.");

      const next = stored
        ? { ...stored, status: requested ? "active" : stored.status }
        : freshFirstWatch();
      setAnnouncement("");
      setCards(response.steps);
      setRun(next);
      setLoading(false);
      if (requested) {
        focusHeading.current = true;
        markAfterRender.current = true;
      }
    } catch (error) {
      if (current !== generation.current) return;
      setAnnouncement("");
      setLoading(false);
      if (requested)
        reportStatus(`First Watch did not start: ${actionError(error)} Open it again to retry.`);
    }
  }, [resetEvidence]);

  useEffect(() => {
    const requested = consumeFirstWatchQuery();
    const onStart = () => resolve(true);
    const onStorage = (event: Event) => {
      if (!(event instanceof StorageEvent)) return;
      if (event.key && !["skein-user", "skein-key", "skein-oidc"].includes(event.key))
        return;
      resolve(false);
    };
    const onIdentityChange = () => resolve(false);
    window.addEventListener(FIRST_WATCH_EVENT, onStart);
    window.addEventListener("storage", onStorage);
    window.addEventListener("skein-identity-change", onIdentityChange);
    const initialGeneration = generation.current;
    queueMicrotask(() => {
      if (generation.current === initialGeneration) resolve(requested);
    });
    return () => {
      generation.current += 1;
      window.removeEventListener(FIRST_WATCH_EVENT, onStart);
      window.removeEventListener("storage", onStorage);
      window.removeEventListener("skein-identity-change", onIdentityChange);
    };
  }, [resolve]);

  useEffect(() => {
    identityRef.current = identity;
    runRef.current = run;
  }, [identity, run]);

  useEffect(() => {
    const onCapture = (event: Event) => {
      const receipt = (
        event as CustomEvent<{
          kind?: string;
          id?: number;
          firstWatchGeneration?: number;
        }>
      ).detail;
      const current = runRef.current;
      if (
        !current ||
        current.stepId !== "capture" ||
        receipt?.firstWatchGeneration !== evidenceGeneration.current
      )
        return;
      if (receipt?.kind !== "task" || !Number.isInteger(receipt.id) || Number(receipt.id) < 1) {
        if (receipt?.kind) {
          const message = `The ${receipt.kind} was saved. Capture a task with todo: to continue.`;
          setStepMessage(message);
          setAnnouncement(message);
        }
        return;
      }
      const message = `Task #${receipt.id} was captured.`;
      setStepMessage(message);
      setAnnouncement("");
      setRun({ ...current, taskId: Number(receipt.id), skippedTaskPractice: false });
    };
    const onPeek = (event: Event) => {
      const receipt = (
        event as CustomEvent<{ taskId?: number; status?: "loaded" | "unavailable" }>
      ).detail;
      const current = runRef.current;
      const pending = pendingPeek.current;
      if (
        !current ||
        !pending ||
        pending.generation !== evidenceGeneration.current ||
        pending.taskId !== receipt?.taskId ||
        current.taskId !== receipt.taskId ||
        pending.source !== current.stepId
      )
        return;
      pendingPeek.current = null;
      if (current.stepId === "task_peek") {
        setPeekResult(receipt.status === "loaded" ? "loaded" : "unavailable");
      } else if (receipt.status === "loaded") {
        searchCompletionPending.current = true;
        setSearchDone(true);
      }
    };
    const onPeekClose = (event: Event) => {
      const result = (event as CustomEvent<{ taskId?: number }>).detail;
      const current = runRef.current;
      if (
        !current ||
        current.stepId !== "search" ||
        current.taskId !== result?.taskId ||
        !searchCompletionPending.current
      )
        return;
      searchCompletionPending.current = false;
      setAnnouncement("Task found in Search. Continue to Inbox.");
    };
    const onSearchResult = (event: Event) => {
      const result = (event as CustomEvent<{ entity?: string; id?: number }>).detail;
      const current = runRef.current;
      if (!current || current.stepId !== "search") return;
      const exact = result?.entity === "task" && result.id === current.taskId;
      pendingPeek.current = exact
        ? {
            generation: evidenceGeneration.current,
            taskId: current.taskId as number,
            source: "search",
          }
        : null;
    };
    const onComposeReady = (event: Event) => {
      const text = (event as CustomEvent<string>).detail;
      const expected = expectedCompose.current;
      const current = runRef.current;
      if (
        !expected ||
        !current ||
        current.stepId !== "bosun" ||
        expected.generation !== evidenceGeneration.current ||
        expected.person !== identityRef.current ||
        text !== expected.text
      )
        return;
      expectedCompose.current = null;
      try {
        window.localStorage.removeItem(firstWatchKey(expected.person));
      } catch {}
      setRun(null);
      setCards(null);
    };
    window.addEventListener("skein-capture-complete", onCapture);
    window.addEventListener("skein-peek-result", onPeek);
    window.addEventListener("skein-peek-close", onPeekClose);
    window.addEventListener("skein-search-result", onSearchResult);
    window.addEventListener("skein-chat-compose-ready", onComposeReady);
    return () => {
      window.removeEventListener("skein-capture-complete", onCapture);
      window.removeEventListener("skein-peek-result", onPeek);
      window.removeEventListener("skein-peek-close", onPeekClose);
      window.removeEventListener("skein-search-result", onSearchResult);
      window.removeEventListener("skein-chat-compose-ready", onComposeReady);
    };
  }, []);

  useEffect(() => {
    if (!identity || !run) return;
    try {
      window.localStorage.setItem(firstWatchKey(identity), JSON.stringify(run));
    } catch {
      reportStatus(
        "First Watch did not save progress in this browser. Keep this tab open to continue.",
      );
    }
  }, [identity, run]);

  useEffect(() => {
    if (run?.stepId !== "bosun") return;
    let live = true;
    api<{ provider: string; provider_error?: string }>("/api/agents/status")
      .then((status) => {
        if (!live) return;
        if (status.provider_error) {
          setChatState("unavailable");
          setComposeText(HELP_PROMPT);
          setAnnouncement("Bosun is unavailable. Chat help is ready.");
        } else if (status.provider === "mock") {
          setChatState("deterministic");
          setComposeText(HELP_PROMPT);
          setAnnouncement(
            "This workspace uses deterministic Chat help. /help is ready.",
          );
        } else {
          setChatState("live");
          setComposeText(BOSUN_PROMPT);
          setAnnouncement("A Bosun question is ready in Chat.");
        }
      })
      .catch(() => {
        if (!live) return;
        setChatState("unknown");
        setComposeText(HELP_PROMPT);
        setAnnouncement("Chat status is unavailable. /help is ready.");
      });
    return () => {
      live = false;
    };
  }, [run?.stepId]);

  useEffect(() => {
    if (!run || !cards) return;
    if (focusHeading.current && run.status === "active") {
      focusHeading.current = false;
      headingRef.current?.focus();
    }
    if (focusResume.current && run.status === "paused") {
      focusResume.current = false;
      resumeRef.current?.focus();
    }
    if (markAfterRender.current) {
      markAfterRender.current = false;
      api("/api/field-guide/first-watch", { method: "POST" }).catch(() => {});
    }
  }, [cards, run]);

  useEffect(() => {
    const expected = focusAfterRoute.current;
    if (!expected) return;
    focusAfterRoute.current = null;
    if (pathname !== expected) return;
    const frame = requestAnimationFrame(() => headingRef.current?.focus());
    return () => cancelAnimationFrame(frame);
  }, [pathname]);

  const liveRegion = (
    <p
      role="status"
      aria-live="polite"
      aria-atomic="true"
      className="sr-only"
      data-testid="first-watch-status"
    >
      {announcement}
    </p>
  );

  if (loading) {
    return (
      <>
        {liveRegion}
        <div data-first-watch className="border-b border-line bg-raised px-4 py-2 text-xs text-ink-3">
          Loading First Watch…
        </div>
      </>
    );
  }
  if (!run || !cards || !identity || identity === "anonymous") return liveRegion;

  if (run.status === "paused") {
    const label = run.stepId === "first_watch" ? "introduction" : `step ${firstWatchStepNumber(run.stepId)} of 6`;
    return (
      <>
        {liveRegion}
        <aside data-first-watch aria-label="First Watch paused" className="border-b border-line bg-raised">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-2 px-4 py-2">
          <button
            ref={resumeRef}
            type="button"
            onClick={() => {
              focusHeading.current = true;
              setRun({ ...run, status: "active" });
            }}
            className="min-h-6 rounded-md border border-line-strong px-2 py-1 text-xs font-medium text-thread hover:bg-card"
          >
            Resume First Watch, {label}
          </button>
          <span className="text-xs text-ink-3">Your task stays in Skein.</span>
          <button
            type="button"
            onClick={() => {
              resetEvidence();
              focusHeading.current = true;
              setRun(freshFirstWatch());
            }}
            className="min-h-6 px-2 py-1 text-xs text-ink-2 underline hover:text-ink"
          >
            Start over
          </button>
          </div>
        </aside>
      </>
    );
  }

  const card = cards.find((item) => item.id === run.stepId) ?? cards[0];
  const intro = run.stepId === "first_watch";
  const number = firstWatchStepNumber(run.stepId);
  const title = intro
    ? "Bosun’s First Watch"
    : `First Watch, step ${number} of 6: ${card.feature}`;
  const changeStep = (stepId: FirstWatchStepId, moveFocus = true) => {
    resetEvidence();
    if (stepId === "bosun") setAnnouncement("Preparing Chat…");
    if (moveFocus) focusHeading.current = true;
    setRun({ ...run, stepId });
  };
  const skipTaskPractice = () => {
    resetEvidence();
    focusHeading.current = true;
    setRun({
      ...run,
      stepId: "review",
      taskId: undefined,
      skippedTaskPractice: true,
    });
  };

  const previousId: FirstWatchStepId | null =
    run.stepId === "capture"
      ? "first_watch"
      : run.stepId === "task_peek"
        ? "capture"
        : run.stepId === "search"
          ? "task_peek"
          : run.stepId === "review"
            ? run.skippedTaskPractice
              ? "capture"
              : "search"
            : run.stepId === "activity_feed"
              ? "review"
              : run.stepId === "bosun"
                ? "activity_feed"
                : null;
  const previousRoute =
    previousId === "first_watch" || previousId === "capture"
      ? "/"
      : previousId === "task_peek" || previousId === "search"
        ? "/dashboard"
        : previousId === "review"
          ? "/review"
          : previousId === "activity_feed"
            ? "/activity"
            : "";
  const goPrevious = (moveFocus: boolean) => {
    if (!previousId) return;
    resetEvidence();
    if (moveFocus) focusHeading.current = true;
    setRun({
      ...run,
      stepId: previousId,
      skippedTaskPractice:
        run.stepId === "review" && run.skippedTaskPractice
          ? false
          : run.skippedTaskPractice,
    });
  };

  const onStep = (stepId: FirstWatchStepId, route = false) => {
    if (route) {
      focusAfterRoute.current =
        stepId === "task_peek"
          ? "/dashboard"
          : stepId === "review"
            ? "/review"
            : stepId === "activity_feed"
              ? "/activity"
              : null;
    }
    if (stepId !== run.stepId) changeStep(stepId, !route);
  };
  const onOpenCapture = () => {
    setStepMessage("");
    window.dispatchEvent(
      new CustomEvent("skein-capture-open", {
        detail: {
          text: "todo: ",
          firstWatchGeneration: evidenceGeneration.current,
        },
      }),
    );
  };
  const onOpenTask = () => {
    setPeekResult("");
    if (!run.taskId) return;
    pendingPeek.current = {
      generation: evidenceGeneration.current,
      taskId: run.taskId,
      source: "task_peek",
    };
    openTaskPeek(run.taskId);
  };
  const onRecapture = () => {
    resetEvidence();
    focusHeading.current = true;
    setRun({
      ...run,
      stepId: "capture",
      taskId: undefined,
      skippedTaskPractice: false,
    });
  };
  const onStartSearch = () => {
    pendingPeek.current = null;
    setSearchDone(false);
    window.dispatchEvent(
      new CustomEvent("skein-search-prefill", { detail: `#${run.taskId}` }),
    );
  };

  return (
    <>
      {liveRegion}
      <aside data-first-watch aria-labelledby="first-watch-title" className="border-b border-line bg-raised">
      <div className="mx-auto max-w-5xl px-4 py-3">
        <div className="flex flex-wrap items-start gap-2">
          <div className="min-w-0 flex-1">
            <p className="font-mono text-[10px] uppercase tracking-wide text-ink-3">Bosun</p>
            <h2
              ref={headingRef}
              id="first-watch-title"
              tabIndex={-1}
              className="font-display text-sm font-semibold text-ink outline-none"
            >
              {title}
            </h2>
          </div>
          <button
            type="button"
            onClick={() => {
              focusResume.current = true;
              setRun({ ...run, status: "paused" });
            }}
            className="min-h-6 shrink-0 px-2 py-1 text-xs text-ink-2 underline hover:text-ink"
          >
            Pause First Watch
          </button>
        </div>

        <p className="mt-1 text-sm text-ink">
          <ShortcutText text={card.pitch} />
        </p>
        {intro ? (
          <>
            <p className="mt-1 text-xs text-ink-2">
              Capture a task that you intend to keep. First Watch does not delete it.
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => {
                  focusHeading.current = true;
                  setRun({ ...run, stepId: "capture" });
                }}
                className="min-h-8 rounded-md bg-thread-solid px-3 py-1.5 text-xs font-semibold text-white hover:opacity-90"
              >
                Start First Watch
              </button>
              <button
                type="button"
                onClick={() => {
                  focusHeading.current = true;
                  setRun({
                    ...run,
                    stepId: "review",
                    taskId: undefined,
                    skippedTaskPractice: true,
                  });
                }}
                className="min-h-8 rounded-md border border-line-strong px-3 py-1.5 text-xs font-medium text-ink hover:bg-card"
              >
                Skip task practice
              </button>
            </div>
          </>
        ) : (
          <>
            <details className="mt-2 text-xs text-ink-2">
              <summary className="cursor-pointer underline">Read the field-guide instructions</summary>
              <p className="mt-1">
                <ShortcutText text={card.how} />
              </p>
            </details>
            <FirstWatchActions
              run={run}
              pathname={pathname}
              stepMessage={stepMessage}
              peekResult={peekResult}
              searchDone={searchDone}
              composeText={composeText}
              chatState={chatState}
              onStep={onStep}
              onSkipTaskPractice={skipTaskPractice}
              onRecapture={onRecapture}
              onOpenCapture={onOpenCapture}
              onOpenTask={onOpenTask}
              onStartSearch={onStartSearch}
              onBeginHandoff={(text) => {
                expectedCompose.current = {
                  generation: evidenceGeneration.current,
                  person: identity,
                  text,
                };
              }}
            />
            {previousId ? (
              <div className="mt-2 border-t border-line pt-2">
                {previousRoute && previousRoute !== pathname ? (
                  <Link
                    href={previousRoute}
                    onClick={() => {
                      focusAfterRoute.current = previousRoute;
                      goPrevious(false);
                    }}
                    className="inline-flex min-h-6 items-center px-2 py-1 text-xs text-ink-2 underline hover:text-ink"
                  >
                    Previous step
                  </Link>
                ) : (
                  <button
                    type="button"
                    onClick={() => goPrevious(true)}
                    className="min-h-6 px-2 py-1 text-xs text-ink-2 underline hover:text-ink"
                  >
                    Previous step
                  </button>
                )}
              </div>
            ) : null}
          </>
        )}
        </div>
      </aside>
    </>
  );
}
