"use client";

import { useSyncExternalStore } from "react";

/** Skein has no roles (trusted-network model), so manager-grade controls are gated by
 *  intent instead: a per-browser toggle. Off by default — a developer never
 *  carries the manager cockpit unless they ask for it. Scope control, NOT
 *  authorization: the endpoints behind it are ordinary CurrentUser writes;
 *  only authority editing requires strong administrator identity server-side. */
const KEY = "skein-manage";

function subscribe(cb: () => void) {
  window.addEventListener("storage", cb);
  return () => window.removeEventListener("storage", cb);
}

export function useManageMode(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => window.localStorage.getItem(KEY) === "1",
    () => false,
  );
}

export function ManageToggle() {
  const on = useManageMode();
  return (
    <button
      onClick={() => {
        if (on) window.localStorage.removeItem(KEY);
        else window.localStorage.setItem(KEY, "1");
        window.dispatchEvent(new Event("storage"));
      }}
      aria-pressed={on}
      title="Show or hide management controls in this browser"
      className={
        "rounded-full px-3 py-1 text-[13px] transition-colors " +
        (on
          ? "bg-weld/15 font-medium text-weld"
          : "bg-raised text-ink-2 hover:bg-line hover:text-ink-2")
      }
    >
      Management view: {on ? "On" : "Off"}
      <span className="sr-only">
        — shows or hides triage verdicts, readouts, and authority editing in
        this browser. It does not grant permissions
      </span>
    </button>
  );
}
