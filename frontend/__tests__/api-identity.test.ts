import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let current = { authenticated: false, user: "anonymous", strong: false, auth_method: "", csrf_token: "" };
let calls: { url: string; init?: RequestInit }[];
beforeEach(() => {
  vi.resetModules(); calls = [];
  current = { authenticated: false, user: "anonymous", strong: false, auth_method: "", csrf_token: "" };
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    if (url.endsWith("/auth/config")) return new Response(JSON.stringify({ mode: "trusted-header", error: "" }));
    if (url.endsWith("/auth/session")) return new Response(JSON.stringify(current));
    return new Response(JSON.stringify({ ok: true }));
  }));
});
afterEach(() => { vi.unstubAllGlobals(); });

describe("the trusted-header identity", () => {
  it("omits the synthetic anonymous name from requests", async () => {
    const { api } = await import("@/lib/api");
    await api("/api/anonymous-probe", { method: "POST" });
    expect(new Headers(calls.at(-1)?.init?.headers).has("X-User")).toBe(false);
  });
  it("uses the same anonymous omission for synchronous pagehide headers", async () => {
    const { bootstrapSession } = await import("@/lib/auth"); await bootstrapSession();
    const { sessionHeaders } = await import("@/lib/api");
    expect(sessionHeaders()).toEqual({ "X-Client": "web" });
  });
  it("sends a name after the person picks one", async () => {
    localStorage.setItem("skein-user", "mario");
    const { api } = await import("@/lib/api"); await api("/api/named-probe", { method: "POST" });
    expect(new Headers(calls.at(-1)?.init?.headers).get("X-User")).toBe("mario");
  });
  it("authenticates raw file responses through the shared session path", async () => {
    current = { authenticated: true, user: "mario", strong: true, auth_method: "api-key", csrf_token: "csrf-mario" };
    const { authenticatedFetch } = await import("@/lib/api"); await authenticatedFetch("/api/file");
    const call = calls.at(-1)!; const headers = new Headers(call.init?.headers);
    expect(headers.get("X-Skein-CSRF")).toBe("csrf-mario");
    expect(headers.get("X-Client")).toBe("web");
    expect(headers.has("Authorization")).toBe(false);
    expect(call.init?.credentials).toBe("include");
  });
  it("rechecks metadata for a rejected raw response without clearing the cookie", async () => {
    current = { authenticated: true, user: "mario", strong: true, auth_method: "oidc", csrf_token: "csrf-mario" };
    const auth = await import("@/lib/auth"); await auth.bootstrapSession();
    const original = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation((input, init) => {
      if (String(input).endsWith("/api/file")) {
        current = { ...current, authenticated: false, strong: false };
        return Promise.resolve(new Response(JSON.stringify({ code: "SESSION_INVALID" }), { status: 401 }));
      }
      return original(input, init);
    });
    const { authenticatedFetch } = await import("@/lib/api");
    expect((await authenticatedFetch("/api/file")).status).toBe(401);
    expect(auth.isSignedIn()).toBe(false);
    expect(auth.sessionSnapshot().csrf_token).toBe("csrf-mario");
    expect(calls.some((c) => c.init?.method === "DELETE")).toBe(false);
  });
});
