import { useCallback, useSyncExternalStore } from "react";

import { getUser, subscribeUser } from "@/lib/api";

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
