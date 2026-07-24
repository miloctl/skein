export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const USER_KEY = "strands-user";

export function getUser(): string {
  if (typeof window === "undefined") return "anonymous";
  return window.localStorage.getItem(USER_KEY) ?? "anonymous";
}

export function setUser(name: string) {
  window.localStorage.setItem(USER_KEY, name.trim() || "anonymous");
}

export async function api<T = unknown>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const token = process.env.NEXT_PUBLIC_API_TOKEN;
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-User": getUser(),
      "X-Client": "web",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
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
