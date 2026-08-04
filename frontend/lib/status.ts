/** The one place a surface says "that worked" or "that failed".
 *
 *  36 call sites used window.alert(), which is worse than it looks: after a
 *  few dialogs browsers offer "prevent this page from creating more dialogs",
 *  and once that is ticked every later failure is swallowed in silence — the
 *  app looks like it is working while writes fail. alert() also blocks, steals
 *  focus, and queues serially when several requests fail together.
 *
 *  A store rather than page state, because 12 of those sites live in nested
 *  components (chat-sidebar, thread-title, standup-card, nav) that have no
 *  page banner to write into and would otherwise need props threaded through
 *  the whole tree.
 */

export type StatusTone = "failure" | "confirmation";
export type Status = { message: string; tone: StatusTone; id: number };

/** Confirmations clear themselves; failures do not. Matches what ThemeSync
 *  used for the theme-adopted note it originally owned. */
const CONFIRMATION_MS = 6000;

let current: Status | null = null;
let seq = 0;
let timer: ReturnType<typeof setTimeout> | null = null;
const listeners = new Set<() => void>();

function emit() {
  for (const listener of [...listeners]) listener();
}

export function subscribeStatus(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Returns the SAME object until something changes. useSyncExternalStore
 *  re-renders whenever the snapshot is referentially new, so building a fresh
 *  object here would loop forever. */
export function getStatus(): Status | null {
  return current;
}

/** Nothing has happened before hydration, and the server cannot know about a
 *  click. A stable null keeps the markup identical on both sides. */
export function getServerStatus(): Status | null {
  return null;
}

/** Say something. A failure holds until dismissed, because the reader has to
 *  see that the thing they asked for did not happen; a confirmation clears
 *  itself. Passing an empty message clears the region. */
export function reportStatus(message: string, tone: StatusTone = "failure") {
  if (timer) {
    clearTimeout(timer);
    timer = null;
  }
  current = message ? { message, tone, id: ++seq } : null;
  emit();
  if (current && tone === "confirmation") {
    timer = setTimeout(() => {
      current = null;
      timer = null;
      emit();
    }, CONFIRMATION_MS);
  }
}

export function dismissStatus() {
  reportStatus("");
}
