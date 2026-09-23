import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const person = (user = "ava", csrf = "csrf-ava") => ({
  authenticated: true, user, strong: true, auth_method: "api-key", csrf_token: csrf,
});
const anonymous = { authenticated: false, user: "anonymous", strong: false, auth_method: null, csrf_token: "" };
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
let served: typeof anonymous | ReturnType<typeof person>;
let calls: { url: string; init?: RequestInit }[];

beforeEach(() => {
  vi.resetModules();
  calls = [];
  served = anonymous;
  Object.defineProperty(navigator, "locks", { configurable: true, value: {
    request: vi.fn(async (_name: string, fn: () => unknown) => fn()),
  } });
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    calls.push({ url: input, init });
    if (input.endsWith("/auth/config")) return response({ mode: "trusted-header", error: "" });
    if (input.endsWith("/auth/session/key")) { served = person(); return response(served); }
    if (input.endsWith("/auth/session") && init?.method === "DELETE") { served = anonymous; return new Response(null, { status: 204 }); }
    if (input.endsWith("/auth/session")) return response(served);
    return response({ ok: true });
  }));
});
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); vi.restoreAllMocks(); });

describe("server-held browser identity", () => {
  it("erases legacy credentials without exchanging or sending them", async () => {
    localStorage.setItem("skein-key", "sk-skein-legacy");
    localStorage.setItem("skein-oidc", JSON.stringify({ access_token: "provider-secret", refresh_token: "refresh-secret" }));
    const { authenticatedFetch } = await import("@/lib/api");
    await authenticatedFetch("/api/tasks");
    expect(localStorage.getItem("skein-key")).toBeNull();
    expect(localStorage.getItem("skein-oidc")).toBeNull();
    expect(JSON.stringify(calls)).not.toMatch(/provider-secret|refresh-secret|sk-skein-legacy/);
  });

  it("binds reads and writes to cookie metadata without a bearer token", async () => {
    served = person();
    vi.stubEnv("NEXT_PUBLIC_API_TOKEN", "shared-token");
    const { authenticatedFetch, getUser } = await import("@/lib/api");
    localStorage.setItem("skein-user", "different-person");
    await authenticatedFetch("/api/tasks");
    await authenticatedFetch("/api/capture", { method: "POST", body: JSON.stringify({ text: "todo: check" }) });
    expect(getUser()).toBe("ava");
    for (const call of calls) expect(call.init?.credentials).toBe("include");
    for (const call of calls.filter((c) => !c.url.includes("/auth/"))) {
      const headers = new Headers(call.init?.headers);
      expect(headers.get("X-Skein-CSRF")).toBe("csrf-ava");
      expect(headers.has("Authorization")).toBe(false);
      expect(headers.has("X-User")).toBe(false);
    }
  });

  it("keeps the trusted-header name picker only without a session", async () => {
    localStorage.setItem("skein-user", "marcus");
    vi.stubEnv("NEXT_PUBLIC_API_TOKEN", "shared-token");
    const { authenticatedFetch } = await import("@/lib/api");
    await authenticatedFetch("/api/tasks");
    const headers = new Headers(calls.at(-1)?.init?.headers);
    expect(headers.get("X-User")).toBe("marcus");
    expect(headers.get("Authorization")).toBe("Bearer shared-token");
    expect(headers.has("X-Skein-CSRF")).toBe(false);
  });

  it("exchanges an entered key once, proves the cookie, and never stores the key", async () => {
    const auth = await import("@/lib/auth");
    await auth.signInWithKey("sk-skein-entered");
    expect(auth.isSignedIn()).toBe(true);
    expect(calls.filter((c) => c.url.endsWith("/session/key"))).toHaveLength(1);
    expect(calls.at(-1)?.url).toMatch(/\/auth\/session$/);
    expect(JSON.stringify(localStorage)).not.toContain("sk-skein-entered");
    expect(JSON.stringify(sessionStorage)).not.toContain("sk-skein-entered");
  });

  it("does not claim sign-in when the browser refused its Secure cookie", async () => {
    vi.mocked(fetch).mockImplementation(async (input) => response(String(input).endsWith("/session/key") ? person() : anonymous));
    const auth = await import("@/lib/auth");
    await expect(auth.signInWithKey("sk-skein-entered")).rejects.toThrow(/HTTPS|same-site/);
    expect(auth.isSignedIn()).toBe(false);
    expect(auth.sessionSnapshot().error).toMatch(/HTTPS|same-site/);
  });

  it("does not send JSON content type for a multipart upload", async () => {
    served = person();
    const { authenticatedFetch } = await import("@/lib/api");
    const body = new FormData(); body.append("file", new Blob(["hello"]), "hello.txt");
    await authenticatedFetch("/api/files", { method: "POST", body });
    const call = calls.at(-1)!;
    expect(new Headers(call.init?.headers).has("Content-Type")).toBe(false);
    expect(new Headers(call.init?.headers).get("X-Skein-CSRF")).toBe("csrf-ava");
  });

  it("logs out an expired cookie with its metadata and no provider call", async () => {
    served = { ...anonymous, csrf_token: "expired-cookie-csrf" };
    const auth = await import("@/lib/auth");
    await auth.signOut();
    const deletion = calls.find((c) => c.init?.method === "DELETE")!;
    expect(new Headers(deletion.init?.headers).get("X-Skein-CSRF")).toBe("expired-cookie-csrf");
    expect(auth.isSignedIn()).toBe(false);
    expect(calls.some((c) => c.url.includes("/auth/token"))).toBe(false);
  });

  it("keeps established identity on a temporary metadata fault", async () => {
    served = person();
    const auth = await import("@/lib/auth");
    await auth.bootstrapSession();
    vi.mocked(fetch).mockResolvedValue(response({ detail: "Unavailable", code: "SESSION_UNAVAILABLE" }, 503));
    await expect(auth.bootstrapSession(true)).rejects.toThrow();
    expect(auth.signedInUser()).toBe("ava");
  });

  it("flushes a pending theme with cached CSRF and a keepalive cookie request", async () => {
    served = person();
    const auth = await import("@/lib/auth"); await auth.bootstrapSession();
    const theme = await import("@/lib/theme");
    theme.setColorway("madder", { fade: false });
    await vi.dynamicImportSettled();
    window.dispatchEvent(new Event("pagehide"));
    const save = calls.find((c) => c.url.endsWith("/api/users/theme"))!;
    expect(save.init?.keepalive).toBe(true);
    expect(save.init?.credentials).toBe("include");
    expect(new Headers(save.init?.headers).get("X-Skein-CSRF")).toBe("csrf-ava");
    expect(new Headers(save.init?.headers).has("Authorization")).toBe(false);
  });

  it("does not flush a former identity's pending theme after a switch", async () => {
    served = person();
    const auth = await import("@/lib/auth"); await auth.bootstrapSession();
    const theme = await import("@/lib/theme");
    theme.setColorway("madder", { fade: false });
    await vi.dynamicImportSettled();
    localStorage.setItem("skein-oidc-generation", "other-session");
    window.dispatchEvent(new Event("pagehide"));
    expect(calls.some((c) => c.url.endsWith("/api/users/theme"))).toBe(false);
  });

  it("keeps the response's request identity across the caller continuation", async () => {
    served = person();
    const auth = await import("@/lib/auth"); await auth.bootstrapSession();
    const result = response({ private: "old data" });
    // The success check runs in api() after authenticatedFetch's continuation.
    // Change identity at that boundary, before body parsing starts.
    vi.spyOn(result, "ok", "get").mockImplementation(() => {
      localStorage.setItem("skein-oidc-generation", "other-session");
      return true;
    });
    vi.mocked(fetch).mockResolvedValueOnce(result);
    const { api } = await import("@/lib/api");
    await expect(api("/api/private/notes")).rejects.toThrow(/identity changed/);
  });

  it("discards a GET body that finishes parsing after an identity change", async () => {
    served = person();
    const auth = await import("@/lib/auth"); await auth.bootstrapSession();
    let parse!: () => void; let started!: () => void;
    const parsing = new Promise<void>((resolve) => { started = resolve; });
    const result = response({});
    vi.spyOn(result, "json").mockImplementation(() => { started(); return new Promise((resolve) => { parse = () => resolve({ private: "old data" }); }); });
    vi.mocked(fetch).mockResolvedValueOnce(result);
    const { api } = await import("@/lib/api");
    const request = api("/api/private/notes");
    const rejected = expect(request).rejects.toThrow(/identity changed/);
    await parsing;
    localStorage.setItem("skein-oidc-generation", "other-session");
    parse(); await rejected;
  });

  it.each(["key sign-in", "sign-out", "expiry"])("clears status receipts on actual session %s", async (transition) => {
    served = person();
    const auth = await import("@/lib/auth");
    await auth.bootstrapSession();
    const status = await import("@/lib/status");
    status.reportStatus("private-review.md is deleted.", "confirmation");
    expect(status.getStatus()?.message).toBe("private-review.md is deleted.");

    if (transition === "key sign-in") {
      const original = vi.mocked(fetch).getMockImplementation()!;
      vi.mocked(fetch).mockImplementation((input, init) => {
        if (String(input).endsWith("/auth/session/key")) {
          served = person("marcus", "csrf-marcus");
          return Promise.resolve(response(served));
        }
        return original(input, init);
      });
      await auth.signInWithKey("sk-skein-marcus");
      expect(auth.signedInUser()).toBe("marcus");
    } else if (transition === "sign-out") {
      await auth.signOut();
      expect(auth.isSignedIn()).toBe(false);
    } else {
      served = { ...anonymous, csrf_token: "csrf-ava" };
      await auth.bootstrapSession(true);
      expect(auth.isSignedIn()).toBe(false);
      expect(auth.sessionEnd()).toBe("expired");
    }
    expect(status.getStatus()).toBeNull();
  });

  it("does not announce a re-read session that did not change", async () => {
    served = person();
    const auth = await import("@/lib/auth");
    await auth.bootstrapSession();
    const heard = vi.fn();
    window.addEventListener("storage", heard);
    await auth.bootstrapSession(true);
    expect(heard).not.toHaveBeenCalled();
    served = person("marcus", "csrf-marcus");
    await auth.bootstrapSession(true);
    expect(heard).toHaveBeenCalledTimes(1);
    window.removeEventListener("storage", heard);
  });

  it("recovers an invalid cookie once without weak fallback, then accepts a changed session", async () => {
    served = { ...anonymous, csrf_token: "expired-cookie-csrf" };
    vi.stubEnv("NEXT_PUBLIC_API_TOKEN", "shared-token");
    localStorage.setItem("skein-user", "marcus");
    const original = vi.mocked(fetch).getMockImplementation()!;
    let code = "SESSION_INVALID";
    vi.mocked(fetch).mockImplementation((input, init) => {
      if (String(input).endsWith("/api/tasks")) {
        calls.push({ url: String(input), init });
        return Promise.resolve(response({ code, detail: "Sign in." }, code === "SESSION_INVALID" ? 401 : 403));
      }
      return original(input, init);
    });
    const auth = await import("@/lib/auth");
    const { api } = await import("@/lib/api");
    await auth.bootstrapSession();
    await expect(api("/api/tasks")).rejects.toThrow("Sign in.");
    expect(calls.filter((c) => c.url.endsWith("/auth/session"))).toHaveLength(2);
    expect(auth.sessionSnapshot()).toMatchObject({ status: "ready", authenticated: false });
    expect(auth.sessionEnd()).toBe("expired");
    const recovered = auth.sessionSnapshot();
    const heard = vi.fn();
    const unsubscribe = auth.subscribeSession(heard);
    try {
      await expect(api("/api/tasks")).rejects.toThrow("Sign in.");
      await expect(api("/api/tasks")).rejects.toThrow("Sign in.");
      expect(calls.filter((c) => c.url.endsWith("/auth/session"))).toHaveLength(2);
      expect(auth.sessionSnapshot()).toBe(recovered);
      expect(heard).not.toHaveBeenCalled();
      for (const call of calls.filter((c) => c.url.endsWith("/api/tasks"))) {
        const headers = new Headers(call.init?.headers);
        expect(headers.has("Authorization")).toBe(false);
        expect(headers.has("X-User")).toBe(false);
        expect(headers.get("X-Skein-CSRF")).toBe("expired-cookie-csrf");
      }
      code = "SESSION_CHANGED";
      served = person();
      await expect(api("/api/tasks")).rejects.toThrow("Sign in.");
      expect(calls.filter((c) => c.url.endsWith("/auth/session"))).toHaveLength(3);
      expect(auth.signedInUser()).toBe("ava");
      expect(auth.sessionEnd()).toBe("");
    } finally {
      unsubscribe();
    }
  });

  it("adopts the team theme for a cookie-free trusted-header visitor", async () => {
    const original = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation((input, init) => String(input).endsWith("/api/users/theme")
      ? Promise.resolve(response({ theme: "", team_default: JSON.stringify({ pack: "ledger", colorway: "madder", appearance: "dark" }) }))
      : original(input, init));
    const auth = await import("@/lib/auth");
    await auth.bootstrapSession();
    expect(auth.isSignedIn()).toBe(false);
    expect(auth.trustedHeaderIdentity()).toBe(true);
    const theme = await import("@/lib/theme");
    expect(await theme.adoptServerTheme()).toBe("team");
    expect(theme.getPack()).toBe("ledger");
    expect(theme.getColorway()).toBe("madder");
    expect(theme.getAppearance()).toBe("dark");
    expect(document.documentElement.dataset.pack).toBe("ledger");
  });
});

describe("a server that stops answering", () => {
  // Real fetch rejects when its signal aborts. A fetch with no signal hangs.
  const silent = (init?: RequestInit) => new Promise<Response>((_, reject) => {
    init?.signal?.addEventListener("abort", () => reject(new DOMException("The operation was aborted.", "AbortError")));
  });
  const stalls = (match: (url: string, init?: RequestInit) => boolean) => {
    const original = vi.mocked(fetch).getMockImplementation()!;
    let stalled = true;
    vi.mocked(fetch).mockImplementation((input, init) =>
      stalled && match(String(input), init) ? silent(init) : original(input, init));
    return () => { stalled = false; };
  };
  // The shared mock runs each callback at once. Sign-in, sign-out, and the
  // session read exclude each other across tabs, which is what a stall holds.
  const exclusiveLocks = () => {
    let tail: Promise<unknown> = Promise.resolve();
    Object.defineProperty(navigator, "locks", { configurable: true, value: {
      request: vi.fn((_name: string, fn: () => Promise<unknown>) => {
        const run = tail.then(fn);
        tail = run.catch(() => {});
        return run;
      }),
    } });
  };
  const settled = (promise: Promise<unknown>) => {
    let state = "pending";
    promise.then(() => { state = "resolved"; }, () => { state = "rejected"; });
    return () => state;
  };
  beforeEach(() => { vi.useFakeTimers(); exclusiveLocks(); });
  afterEach(() => { vi.useRealTimers(); });

  it("ends a stalled session read with the unreachable wording and frees the lock", async () => {
    const recover = stalls((url, init) => url.endsWith("/auth/session") && !init?.method);
    const auth = await import("@/lib/auth");
    const first = settled(auth.bootstrapSession());
    await vi.advanceTimersByTimeAsync(60_000);
    expect(first()).toBe("rejected");
    expect(auth.sessionSnapshot()).toMatchObject({ status: "unavailable" });
    expect(auth.sessionSnapshot().error).toMatch(/^Cannot reach the backend at .*did not answer in 60 seconds/);
    recover();
    served = person();
    await auth.bootstrapSession(true);
    expect(auth.signedInUser()).toBe("ava");
  });

  it("ends a stalled sign-in configuration read so Try again can start over", async () => {
    const recover = stalls((url) => url.endsWith("/auth/config"));
    const auth = await import("@/lib/auth");
    const first = settled(auth.bootstrapSession());
    await vi.advanceTimersByTimeAsync(60_000);
    expect(first()).toBe("rejected");
    expect(auth.sessionSnapshot().error).toBe("Cannot read the sign-in configuration. Check that the server is running, then try again.");
    recover();
    await expect(auth.bootstrapSession(true)).resolves.toMatchObject({ status: "ready" });
  });

  it.each([
    ["key sign-in", (url: string) => url.endsWith("/auth/session/key"), (auth: typeof import("@/lib/auth")) => auth.signInWithKey("sk-skein-entered")],
    ["sign-out", (url: string, init?: RequestInit) => init?.method === "DELETE", (auth: typeof import("@/lib/auth")) => auth.signOut()],
  ])("releases the cross-tab lock after a stalled %s", async (_name, match, act) => {
    served = person();
    const recover = stalls(match);
    const auth = await import("@/lib/auth");
    const stalled = settled(act(auth));
    await vi.advanceTimersByTimeAsync(60_000);
    expect(stalled()).toBe("rejected");
    recover();
    const next = settled(auth.bootstrapSession(true));
    await vi.advanceTimersByTimeAsync(0);
    expect(next()).toBe("resolved");
  });

  it("keeps a slow sign-in exchange that still answers inside the deadline", async () => {
    // A 30-second wait for a database connection, then identity-provider calls
    // the backend caps at 5 seconds each.
    const original = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation((input, init) => String(input).endsWith("/auth/session/key")
      ? new Promise((resolve) => setTimeout(() => resolve(original(input, init)), 50_000))
      : original(input, init));
    const auth = await import("@/lib/auth");
    const exchange = settled(auth.signInWithKey("sk-skein-entered"));
    await vi.advanceTimersByTimeAsync(50_000);
    expect(exchange()).toBe("resolved");
    expect(auth.signedInUser()).toBe("ava");
  });
});
