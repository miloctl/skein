"use client";

import { useSyncExternalStore } from "react";

import { dismissStatus, getServerStatus, getStatus, subscribeStatus } from "@/lib/status";

/** The single renderer for lib/status.ts, mounted once in the app shell.
 *
 *  The two live nodes stay mounted before text arrives. Screen readers can miss
 *  a region that is created with its first message already inside it. A keyed
 *  child still replaces equal consecutive messages, so the second message is a
 *  real DOM change without remounting the live region itself.
 *
 *  Every node carries data-status-region because each one is a body child,
 *  and the two overlays (task-peek.tsx, capture-palette.tsx) inert every body
 *  child but their own. An inert live region announces nothing, and an inert
 *  toast cannot be dismissed. */
export function StatusRegion() {
  const status = useSyncExternalStore(subscribeStatus, getStatus, getServerStatus);
  const failure = status?.tone === "failure";
  return (
    <>
      <div data-status-region role="status" aria-live="polite" aria-atomic="true" className="sr-only">
        {status?.tone === "confirmation" ? (
          <span key={status.id}>{status.message}</span>
        ) : null}
      </div>
      <div data-status-region role="alert" aria-live="assertive" aria-atomic="true" className="sr-only">
        {failure && status ? <span key={status.id}>{status.message}</span> : null}
      </div>
      {status ? (
        <div
          data-status-region
          className={
            "fixed bottom-4 left-1/2 z-50 flex max-w-[min(92vw,44rem)] -translate-x-1/2" +
            " items-center gap-3 rounded-xl border px-4 py-2 text-xs shadow-float " +
            (failure
              ? "border-danger/30 bg-danger/10 text-danger"
              : "border-line bg-card text-ink-2")
          }
        >
          <span>{status.message}</span>
          {failure && (
            <button onClick={dismissStatus} className="shrink-0 text-xs underline">
              dismiss
            </button>
          )}
        </div>
      ) : null}
    </>
  );
}
