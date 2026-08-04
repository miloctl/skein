import { accessToken } from "./auth";
import { API_URL } from "./config";

export { API_URL };

/** One condition, one wording (CLAUDE.md). Every surface that cannot reach the
 *  backend says this — the URL included, because in a self-hosted deployment
 *  it is usually the thing that is wrong. */
export const backendUnreachable = (error?: unknown) =>
  `Cannot reach the backend at ${API_URL}. Check that the server is running, then try again.` +
  (error ? ` (${detail(error)})` : "");

/** True only for a transport failure. `api()` throws a plain Error carrying the
 *  server's own detail for anything the backend actually answered, and calling
 *  that "unreachable" tells the reader to go check a server that is running. */
export const isUnreachable = (error: unknown) => error instanceof TypeError;

/** The server's own words. String(error) on an Error prepends the class name,
 *  so every surface that interpolated one showed the reader "Error: Failed to
 *  fetch" — the "Error: " is JS internals, and nothing the reader can act on. */
const detail = (error: unknown) => (error instanceof Error ? error.message : String(error));

/** What a failed page LOAD says. A refusal the server actually answered is not
 *  an unreachable backend, and reporting it as one sends the reader to check a
 *  server that is running and replying. */
export const loadError = (error: unknown) =>
  isUnreachable(error) ? backendUnreachable(error) : `Could not load this page. ${detail(error)}`;

/** What a failed ACTION says — a write the reader just triggered, where "could
 *  not load this page" would name the wrong thing. A refusal the server
 *  answered is already a sentence written for this reader ("decision #1 is
 *  already superseded"), so it stands on its own. */
export const actionError = (error: unknown) =>
  isUnreachable(error) ? backendUnreachable(error) : detail(error);

const USER_KEY = "skein-user";
const API_KEY_KEY = "skein-key";

export function getUser(): string {
  if (typeof window === "undefined") return "anonymous";
  return window.localStorage.getItem(USER_KEY) ?? "anonymous";
}

export function setUser(name: string) {
  window.localStorage.setItem(USER_KEY, name.trim() || "anonymous");
  // storage events don't fire in the writing tab — nudge same-tab
  // subscribers (nav chip, guide page) like setApiKey does
  window.dispatchEvent(new Event("storage"));
}

// notifies on identity changes (cross-tab natively, same-tab via the
// synthetic event the writers dispatch)
export function subscribeUser(cb: () => void) {
  window.addEventListener("storage", cb);
  return () => window.removeEventListener("storage", cb);
}

// Personal API key (sk-skein-…): the strong identity for private surfaces
// (People page, fb: capture). Interim until OIDC+PKCE lands at deployment.
export function getApiKey(): string {
  if (typeof window === "undefined") return "";
  return window.localStorage.getItem(API_KEY_KEY) ?? "";
}

export function setApiKey(key: string) {
  const k = key.trim();
  if (k) window.localStorage.setItem(API_KEY_KEY, k);
  else window.localStorage.removeItem(API_KEY_KEY);
  // storage events don't fire in the writing tab — nudge same-tab
  // subscribers (nav dot, Settings key status) like every other writer
  window.dispatchEvent(new Event("storage"));
}

/** The credential this request carries, strongest first.
 *
 *  A signed-in OIDC session wins: it is the deployment's own identity model
 *  wherever it is on, and it is the most recent deliberate act. A personal
 *  key comes next — it is per-person and proves identity. The shared token is
 *  last and proves only that the caller reached the app, since it ships inside
 *  the public JS bundle. */
export async function bearer(): Promise<string> {
  return (await accessToken()) || getApiKey() || process.env.NEXT_PUBLIC_API_TOKEN || "";
}

/** Short-lived GET cache. Pages fan out to the same handful of list
 *  endpoints (dashboard alone reads 13), and a tab switch refires them all;
 *  within this window the previous body is the answer. */
const GET_CACHE_TTL_MS = 15_000;
const getCache = new Map<string, { at: number; entry: Promise<unknown> }>();

if (typeof window !== "undefined") {
  // Identity rides on every request (X-User, bearer), so a cached body
  // belongs to ONE identity. Every identity writer — setUser and setApiKey
  // here, writeStored in lib/auth.ts — dispatches "storage"; without this
  // clear, someone who switches identity reads the previous identity's
  // data for up to GET_CACHE_TTL_MS.
  window.addEventListener("storage", () => getCache.clear());
  // The chat stream (app/runtime-provider.tsx) posts through raw fetch,
  // not api(), so the non-GET clear below never sees it — this event is
  // that write's only signal.
  window.addEventListener("skein-chat-activity", () => getCache.clear());
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
  const hit = getCache.get(path);
  if (hit && Date.now() - hit.at < GET_CACHE_TTL_MS) return hit.entry as Promise<T>;
  const entry = request<T>(path, init);
  getCache.set(path, { at: Date.now(), entry });
  // a failure proves nothing about the next call — never serve it from cache
  entry.catch(() => {
    if (getCache.get(path)?.entry === entry) getCache.delete(path);
  });
  return entry;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const auth = await bearer();
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-User": getUser(),
      "X-Client": "web",
      ...(auth ? { Authorization: `Bearer ${auth}` } : {}),
      ...init?.headers,
    },
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const d = (await res.json()).detail;
      // FastAPI 422s send an array of objects — stringify, never "[object Object]"
      if (typeof d === "string") detail = d;
      else if (d !== undefined) detail = JSON.stringify(d);
    } catch {}
    throw new Error(detail);
  }
  return res.json();
}
