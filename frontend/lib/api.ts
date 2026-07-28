export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const USER_KEY = "strands-user";
const API_KEY_KEY = "strands-key";

export function getUser(): string {
  if (typeof window === "undefined") return "anonymous";
  return window.localStorage.getItem(USER_KEY) ?? "anonymous";
}

export function setUser(name: string) {
  window.localStorage.setItem(USER_KEY, name.trim() || "anonymous");
}

// notifies on cross-tab identity changes; same-tab changes reload the page
export function subscribeUser(cb: () => void) {
  window.addEventListener("storage", cb);
  return () => window.removeEventListener("storage", cb);
}

// Personal API key (sk-strands-…): the strong identity for private surfaces
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

export async function api<T = unknown>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const token = process.env.NEXT_PUBLIC_API_TOKEN;
  const personal = getApiKey();
  const auth = personal || token;
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
