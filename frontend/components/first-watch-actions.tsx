"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";

import type { FirstWatchRun, FirstWatchStepId } from "@/lib/first-watch";

type Props = {
  run: FirstWatchRun;
  pathname: string;
  stepMessage: string;
  peekResult: "" | "loaded" | "unavailable";
  searchDone: boolean;
  composeText: string | null;
  chatState: "loading" | "live" | "deterministic" | "unavailable" | "unknown";
  onStep: (stepId: FirstWatchStepId, route?: boolean) => void;
  onSkipTaskPractice: () => void;
  onRecapture: () => void;
  onOpenCapture: () => void;
  onOpenTask: () => void;
  onStartSearch: () => void;
  onBeginHandoff: (text: string) => void;
};

const primaryClass =
  "inline-flex min-h-8 items-center rounded-md bg-thread-solid px-3 py-1.5 text-xs font-semibold text-white hover:opacity-90";
const secondaryClass =
  "inline-flex min-h-8 items-center rounded-md border border-line-strong px-3 py-1.5 text-xs font-medium text-ink hover:bg-card";

export function FirstWatchActions({
  run,
  pathname,
  stepMessage,
  peekResult,
  searchDone,
  composeText,
  chatState,
  onStep,
  onSkipTaskPractice,
  onRecapture,
  onOpenCapture,
  onOpenTask,
  onStartSearch,
  onBeginHandoff,
}: Props) {
  const router = useRouter();

  if (run.stepId === "capture")
    return (
      <>
        {stepMessage ? (
          <p className="mt-2 text-xs text-ink-2">
            {stepMessage}
          </p>
        ) : null}
        <div className="mt-3 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => {
              if (!run.taskId) {
                onOpenCapture();
                return;
              }
              onStep("task_peek", true);
              router.push("/dashboard");
            }}
            className={primaryClass}
          >
            {run.taskId ? "Continue to Work" : "Open Capture"}
          </button>
          <button type="button" onClick={onSkipTaskPractice} className={secondaryClass}>
            Skip task practice
          </button>
        </div>
      </>
    );

  if (run.stepId === "task_peek")
    return (
      <>
        {peekResult === "unavailable" ? (
          <p role="alert" className="mt-2 text-xs text-danger">
            This task is not available. Capture another task or skip task practice.
          </p>
        ) : null}
        <div className="mt-3 flex flex-wrap gap-2">
          {!run.taskId ? (
            <button type="button" onClick={onRecapture} className={primaryClass}>
              Capture another task
            </button>
          ) : pathname !== "/dashboard" ? (
            <Link
              href="/dashboard"
              onClick={() => onStep("task_peek", true)}
              className={primaryClass}
            >
              Open Work
            </Link>
          ) : peekResult === "loaded" ? (
            <button type="button" onClick={() => onStep("search")} className={primaryClass}>
              Continue to Search
            </button>
          ) : (
            <button type="button" onClick={onOpenTask} className={primaryClass}>
              Open task #{run.taskId}
            </button>
          )}
          {peekResult === "unavailable" ? (
            <button type="button" onClick={onRecapture} className={secondaryClass}>
              Capture another task
            </button>
          ) : null}
          <button type="button" onClick={onSkipTaskPractice} className={secondaryClass}>
            Skip task practice
          </button>
        </div>
      </>
    );

  if (run.stepId === "search")
    return (
      <div className="mt-3 flex flex-wrap gap-2">
        {!run.taskId ? (
          <button type="button" onClick={onRecapture} className={primaryClass}>
            Capture a task
          </button>
        ) : searchDone ? (
          <Link
            href="/review"
            onClick={() => onStep("review", true)}
            className={primaryClass}
          >
            Continue to Inbox
          </Link>
        ) : (
          <button type="button" onClick={onStartSearch} className={primaryClass}>
            Put #{run.taskId} in Search
          </button>
        )}
        <button type="button" onClick={onSkipTaskPractice} className={secondaryClass}>
          Skip task practice
        </button>
      </div>
    );

  if (run.stepId === "review")
    return (
      <div className="mt-3 flex flex-wrap gap-2">
        {pathname === "/review" ? (
          <Link
            href="/activity"
            onClick={() => onStep("activity_feed", true)}
            className={primaryClass}
          >
            Continue to Team
          </Link>
        ) : (
          <Link href="/review" onClick={() => onStep("review", true)} className={primaryClass}>
            Open Approvals
          </Link>
        )}
      </div>
    );

  if (run.stepId === "activity_feed")
    return (
      <div className="mt-3 flex flex-wrap gap-2">
        {pathname === "/activity" ? (
          <button type="button" onClick={() => onStep("bosun")} className={primaryClass}>
            Continue to Chat
          </button>
        ) : (
          <Link
            href="/activity"
            onClick={() => onStep("activity_feed", true)}
            className={primaryClass}
          >
            Open Team activity
          </Link>
        )}
      </div>
    );

  if (run.stepId !== "bosun") return null;
  if (composeText === null)
    return (
      <p className="mt-2 text-xs text-ink-3">
        Preparing Chat…
      </p>
    );

  const label = chatState === "live" ? "Ask Bosun" : "Open Chat help";
  const message =
    chatState === "live"
      ? "A Bosun question is ready in Chat."
      : chatState === "deterministic"
        ? "This workspace uses deterministic Chat help. /help is ready."
        : chatState === "unavailable"
          ? "Bosun is unavailable. Chat help is ready."
          : "Chat status is unavailable. /help is ready.";
  return (
    <>
      <p className="mt-2 text-xs text-ink-2">{message}</p>
      <div className="mt-3 flex flex-wrap gap-2">
        {pathname === "/chat" ? (
          <button
            type="button"
            onClick={() => {
              onBeginHandoff(composeText);
              window.dispatchEvent(
                new CustomEvent("skein-chat-compose", { detail: composeText }),
              );
            }}
            className={primaryClass}
          >
            {label}
          </button>
        ) : (
          <Link
            href={{ pathname: "/chat", query: { compose: composeText } }}
            onClick={() => onBeginHandoff(composeText)}
            className={primaryClass}
          >
            {label}
          </Link>
        )}
      </div>
    </>
  );
}
