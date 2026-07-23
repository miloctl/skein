export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** Stable per-browser thread id so the backend session persists across reloads. */
export function getThreadId(): string {
  if (typeof window === "undefined") return "server";
  const key = "strands-thread-id";
  let id = window.localStorage.getItem(key);
  if (!id) {
    id = crypto.randomUUID();
    window.localStorage.setItem(key, id);
  }
  return id;
}
