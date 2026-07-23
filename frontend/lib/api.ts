export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const USER_KEY = "strands-user";
const THREAD_KEY = "strands-thread-id";

export function getUser(): string {
  if (typeof window === "undefined") return "anonymous";
  return window.localStorage.getItem(USER_KEY) ?? "anonymous";
}

export function setUser(name: string) {
  window.localStorage.setItem(USER_KEY, name.trim() || "anonymous");
}

/** Stable per-browser thread id so the backend chat session persists. */
export function getThreadId(): string {
  if (typeof window === "undefined") return "server";
  let id = window.localStorage.getItem(THREAD_KEY);
  if (!id) {
    id = crypto.randomUUID();
    window.localStorage.setItem(THREAD_KEY, id);
  }
  return id;
}

export async function api<T = unknown>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-User": getUser(),
      ...init?.headers,
    },
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {}
    throw new Error(detail);
  }
  return res.json();
}
