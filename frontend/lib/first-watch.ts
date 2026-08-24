export const FIRST_WATCH_EVENT = "skein-first-watch-start";
const FIRST_WATCH_STORAGE_PREFIX = "skein-first-watch:";
const FIRST_WATCH_VERSION = 1;
export const BOSUN_PROMPT =
  "/as bosun I finished First Watch. Which Skein feature can I try next?";
export const HELP_PROMPT = "/help";
export const FIRST_WATCH_STEP_IDS = [
  "first_watch",
  "capture",
  "task_peek",
  "search",
  "review",
  "activity_feed",
  "bosun",
] as const;

export type FirstWatchStepId = (typeof FIRST_WATCH_STEP_IDS)[number];
export type FirstWatchCard = {
  id: FirstWatchStepId;
  feature: string;
  knot: string;
  pitch: string;
  how: string;
  link: string;
};
export type FirstWatchRun = {
  version: 1;
  status: "active" | "paused";
  stepId: FirstWatchStepId;
  taskId?: number;
  skippedTaskPractice: boolean;
};

export function startFirstWatch() {
  window.dispatchEvent(new Event(FIRST_WATCH_EVENT));
}

export function firstWatchKey(person: string) {
  return `${FIRST_WATCH_STORAGE_PREFIX}${person}`;
}

export function freshFirstWatch(): FirstWatchRun {
  return {
    version: FIRST_WATCH_VERSION,
    status: "active",
    stepId: "first_watch",
    skippedTaskPractice: false,
  };
}

export function parseFirstWatch(value: string | null): FirstWatchRun | null {
  if (!value) return null;
  try {
    const parsed = JSON.parse(value) as Partial<FirstWatchRun>;
    if (
      parsed.version !== FIRST_WATCH_VERSION ||
      (parsed.status !== "active" && parsed.status !== "paused") ||
      !FIRST_WATCH_STEP_IDS.includes(parsed.stepId as FirstWatchStepId) ||
      typeof parsed.skippedTaskPractice !== "boolean" ||
      (parsed.taskId !== undefined &&
        (!Number.isInteger(parsed.taskId) || parsed.taskId < 1))
    )
      return null;
    return {
      version: FIRST_WATCH_VERSION,
      status: parsed.status,
      stepId: parsed.stepId as FirstWatchStepId,
      ...(parsed.taskId === undefined ? {} : { taskId: parsed.taskId }),
      skippedTaskPractice: parsed.skippedTaskPractice,
    };
  } catch {
    return null;
  }
}

export function consumeFirstWatchQuery(): boolean {
  const url = new URL(window.location.href);
  if (url.searchParams.get("tour") !== "first-watch") return false;
  url.searchParams.delete("tour");
  window.history.replaceState(window.history.state, "", url);
  return true;
}

export function firstWatchStepNumber(stepId: FirstWatchStepId): number {
  return FIRST_WATCH_STEP_IDS.indexOf(stepId);
}
