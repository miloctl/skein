import { useCallback, useSyncExternalStore } from "react";

import { getUser, subscribeUser } from "@/lib/api";
import { sessionSnapshot, subscribeSession } from "@/lib/auth";

/** The audience a person last picked on one form (standup, capture, time
 *  away), remembered per browser. Until they pick, each of these forms starts
 *  at "only you" (docs/VISIBILITY.md). Keyed by the signed-in name: on a
 *  shared browser, one person's "everyone on the roster" must not become the
 *  next person's default. `valid` rejects a value an older build wrote.
 *
 *  Its own event, not "storage": lib/api.ts drops its GET cache on every
 *  "storage" event, so a picker change would refetch the whole page. */
const EVENT = "skein-audience-change";

function subscribe(cb: () => void) {
  window.addEventListener(EVENT, cb);
  window.addEventListener("storage", cb);
  return () => {
    window.removeEventListener(EVENT, cb);
    window.removeEventListener("storage", cb);
  };
}

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function useRememberedAudience<T>(
  form: string,
  fallback: T,
  valid: (value: unknown) => boolean,
): [T, (value: T) => void] {
  const user = useSyncExternalStore(subscribeUser, getUser, () => "anonymous");
  const key = `skein-audience-${form}-${user}`;
  const raw = useSyncExternalStore(
    subscribe,
    () => read(key),
    () => null,
  );
  let value = fallback;
  try {
    const parsed: unknown = raw ? JSON.parse(raw) : undefined;
    if (raw && valid(parsed)) value = parsed as T;
  } catch {
    /* a value an older build wrote: start at the fallback */
  }
  const remember = useCallback(
    (next: T) => {
      try {
        localStorage.setItem(key, JSON.stringify(next));
      } catch {
        /* private mode: the form starts at "only you" again next time */
      }
      window.dispatchEvent(new Event(EVENT));
    },
    [key],
  );
  return [value, remember];
}

export type TierChoice = { visibility: string; crew_id: number };

export const ONLY_YOU: TierChoice = { visibility: "private", crew_id: 0 };
export const ROSTER: TierChoice = { visibility: "workspace", crew_id: 0 };

/** Whether "only you" can be the start. A weak identity (a trusted-header
 *  name with no key) reads no private row, so a private default hid a
 *  person's own standup from them. The server applies the same rule when a
 *  request names no tier (routes/api.py::_personal_default). */
export function useStrongIdentity(): boolean {
  return useSyncExternalStore(
    subscribeSession,
    () => sessionSnapshot().strong,
    () => false,
  );
}

export function isTierChoice(value: unknown): boolean {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  return (
    ["private", "crew", "workspace"].includes(String(v.visibility)) &&
    typeof v.crew_id === "number" &&
    (v.visibility === "crew") === v.crew_id > 0
  );
}
