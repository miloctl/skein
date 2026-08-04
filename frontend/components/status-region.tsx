"use client";

import { useSyncExternalStore } from "react";

import { dismissStatus, getServerStatus, getStatus, subscribeStatus } from "@/lib/status";

/** The single renderer for lib/status.ts, mounted once in the app shell.
 *
 *  role decides how assistive tech treats it, and the two cases genuinely
 *  differ: a failure of something the reader just asked for must interrupt
 *  ("alert" is assertive), a confirmation waits its turn ("status" is
 *  polite). window.alert() was announced; a plain styled div is not, so a
 *  replacement without a live role is a downgrade for a screen-reader
 *  user, not an upgrade.
 *
 *  key={id} remounts the node per message. Without it, two failures in a row
 *  with the same text change nothing in the DOM and the live region stays
 *  silent the second time. */
export function StatusRegion() {
  const status = useSyncExternalStore(subscribeStatus, getStatus, getServerStatus);
  if (!status) return null;
  const failure = status.tone === "failure";
  return (
    <div
      key={status.id}
      role={failure ? "alert" : "status"}
      className={
        "fixed bottom-4 left-1/2 z-50 flex max-w-[min(92vw,44rem)] -translate-x-1/2" +
        " items-center gap-3 rounded-xl border px-4 py-2 text-xs shadow-float " +
        (failure ? "border-danger/30 bg-danger/10 text-danger" : "border-line bg-card text-ink-2")
      }
    >
      <span>{status.message}</span>
      {failure && (
        <button onClick={dismissStatus} className="shrink-0 text-xs underline">
          dismiss
        </button>
      )}
    </div>
  );
}
