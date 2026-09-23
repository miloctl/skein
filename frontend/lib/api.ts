import {
  bootstrapSession, checkSessionRevision, sessionGeneration, sessionRejected, sessionRevision, sessionSnapshot,
  signedInUser, trustedHeaderIdentity,
} from "./auth";
import { API_URL, backendUnreachable } from "./config";

export { API_URL, backendUnreachable };

/** True only for a transport failure. `api()` throws a plain Error carrying the
 *  server's own detail for anything the backend actually answered, and calling
 *  that "unreachable" tells the reader to go check a server that is running. */
export const isUnreachable = (error: unknown) => error instanceof TypeError;

/** The server's own words. String(error) on an Error prepends the class name,
 *  so every surface that interpolated one showed the reader "Error: Failed to
 *  fetch" — the "Error: " is JS internals, and nothing the reader can act on. */
const detail = (error: unknown) =>
  error instanceof Error ? error.message : String(error);

/** What a failed page LOAD says. A refusal the server actually answered is not
 *  an unreachable backend, and reporting it as one sends the reader to check a
 *  server that is running and replying. */
export const loadError = (error: unknown) =>
  isUnreachable(error)
    ? backendUnreachable(error)
    : `Could not load this page: ${detail(error)}`;

/** What a failed ACTION says — a write the reader just triggered, where "could
 *  not load this page" would name the wrong thing. A refusal the server
 *  answered is already a sentence written for this reader ("decision #1 is
 *  already superseded"), so it stands on its own. */
export const actionError = (error: unknown) =>
  isUnreachable(error) ? backendUnreachable(error) : detail(error);

export async function errorFromResponse(res: Response): Promise<Error> {
  let message = `${res.status} ${res.statusText}`;
  try {
    const body = await res.json();
    const served = body.detail;
    if (typeof served === "string") message = served;
    else if (served !== undefined) message = JSON.stringify(served);
  } catch {}
  return new Error(message);
}

const USER_KEY = "skein-user";

export function getUser(): string {
  if (typeof window === "undefined") return "anonymous";
  if (signedInUser()) return signedInUser();
  if (!trustedHeaderIdentity()) return "anonymous";
  try { return window.localStorage.getItem(USER_KEY) ?? "anonymous"; } catch { return "anonymous"; }
}

export function setUser(name: string) {
  window.localStorage.setItem(USER_KEY, name.trim() || "anonymous");
  // storage events don't fire in the writing tab — nudge same-tab
  // subscribers (nav chip, guide page) read the new identity immediately
  window.dispatchEvent(new Event("storage"));
  window.dispatchEvent(new Event("skein-identity-change"));
}

// notifies on identity changes (cross-tab natively, same-tab via the
// synthetic event the writers dispatch)
export function subscribeUser(cb: () => void) {
  window.addEventListener("storage", cb);
  return () => window.removeEventListener("storage", cb);
}

/** Synchronous for pagehide: a closing page cannot await bootstrap or refresh. */
export function sessionHeaders(): Record<string, string> {
  const csrf = sessionSnapshot().csrf_token;
  if (csrf) return { "X-Skein-CSRF": csrf, "X-Client": "web" };
  if (!trustedHeaderIdentity()) return { "X-Client": "web" };
  const user = getUser();
  const shared = process.env.NEXT_PUBLIC_API_TOKEN;
  return {
    "X-Client": "web",
    ...(user === "anonymous" ? {} : { "X-User": user }),
    ...(shared ? { Authorization: `Bearer ${shared}` } : {}),
  };
}

/** Short-lived GET cache. Pages fan out to the same handful of list
 *  endpoints (dashboard alone reads 13), and a tab switch refires them all;
 *  within this window the previous body is the answer. */
const GET_CACHE_TTL_MS = 15_000;
const getCache = new Map<string, { at: number; entry: Promise<unknown> }>();
const responseRevisions = new WeakMap<Response, string>();

if (typeof window !== "undefined") {
  // Cookie metadata changes in lib/auth.ts and name changes here dispatch
  // storage. Without this clear a switched identity receives the old cache.
  window.addEventListener("storage", () => getCache.clear());
  // The chat stream (app/runtime-provider.tsx) posts through raw fetch,
  // not api(), so the non-GET clear below never sees it — this event is
  // that write's only signal.
  window.addEventListener("skein-chat-activity", () => getCache.clear());
  // lib/attention.ts relays another tab's write as this event. That write
  // never passed through this tab's api(), so without the clear the refresh
  // it triggers (nav badge, My Day) reads the pre-write body for 15 s.
  window.addEventListener("skein-attention-change", () => getCache.clear());
}

export async function authenticatedFetch(
  path: string,
  init?: RequestInit,
): Promise<Response> {
  const startedGeneration = sessionGeneration();
  const started = sessionRevision();
  const loading = sessionSnapshot().status === "loading";
  await bootstrapSession();
  if (startedGeneration !== sessionGeneration() || (!loading && started !== sessionRevision())) throw new Error("Your browser identity changed. Check your identity, then try again.");
  const sent = sessionRevision();
  const headers = new Headers(init?.headers);
  if (!(init?.body instanceof FormData) && !headers.has("Content-Type"))
    headers.set("Content-Type", "application/json");
  for (const [name, value] of Object.entries(sessionHeaders())) headers.set(name, value);
  const response = await fetch(`${API_URL}${path}`, { ...init, credentials: "include", headers });
  checkSessionRevision(sent);
  responseRevisions.set(response, sent);
  if (response.status === 401 || response.status === 403) await sessionRejected(response, sent);
  return response;
}

export async function api<T = unknown>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  if ((init?.method ?? "GET").toUpperCase() !== "GET") {
    // clear on BOTH sides of a write: before, so nothing stale outlives it;
    // after it settles, so a GET that started mid-write cannot pin a
    // pre-write body under a fresh timestamp
    getCache.clear();
    try {
      return await request<T>(path, init);
    } finally {
      getCache.clear();
    }
  }
  // Cursor polling asks the server for state that can change in another
  // browser. Serving that path from this tab's 15-second cache makes a live
  // private chat look frozen after the first empty poll.
  if (init?.cache === "no-store") return request<T>(path, init);
  const hit = getCache.get(path);
  if (hit && Date.now() - hit.at < GET_CACHE_TTL_MS)
    return hit.entry as Promise<T>;
  const entry = request<T>(path, init);
  getCache.set(path, { at: Date.now(), entry });
  // a failure proves nothing about the next call — never serve it from cache
  entry.catch(() => {
    if (getCache.get(path)?.entry === entry) getCache.delete(path);
  });
  return entry;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await authenticatedFetch(path, init);
  if (!response.ok) throw await errorFromResponse(response);
  // Identity can change between authenticatedFetch resolving and this
  // continuation. The request's revision, not the current one, owns its body.
  const sent = responseRevisions.get(response)!;
  const body = await response.json();
  checkSessionRevision(sent);
  return body;
}
