import { readFileSync } from "node:fs";
import { webcrypto } from "node:crypto";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const anonymous = { authenticated: false, user: "anonymous", strong: false, auth_method: "", csrf_token: "" };
const person = (user = "ava", csrf = "csrf-ava") => ({ authenticated: true, user, strong: true, auth_method: "oidc", csrf_token: csrf });
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((r) => { resolve = r; }); return { promise, resolve }; }
let cookie = anonymous;
let calls: { url: string; init?: RequestInit }[];
let exchange: (() => Promise<Response>) | null;
let protectedResponse: (() => Promise<Response>) | null;
const locks = Object.getOwnPropertyDescriptor(navigator, "locks")!;

beforeEach(() => {
  vi.resetModules(); cookie = anonymous; calls = []; exchange = null; protectedResponse = null;
  Object.defineProperty(navigator, "locks", locks);
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    if (url.endsWith("/auth/config")) return json({ mode: "oidc", error: "", client_id: "skein-web", authorize_url: "https://idp.example.com/authorize", scopes: "openid profile" });
    if (url.endsWith("/auth/token") || url.endsWith("/session/key")) {
      if (exchange) return exchange();
      cookie = person(); return json(cookie);
    }
    if (url.endsWith("/auth/session") && init?.method === "DELETE") { cookie = anonymous; return new Response(null, { status: 204 }); }
    if (url.endsWith("/auth/sessions") && init?.method === "DELETE") { cookie = anonymous; return new Response(null, { status: 204 }); }
    if (url.endsWith("/auth/session")) return json(cookie);
    return protectedResponse ? protectedResponse() : json({ ok: true });
  }));
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); Object.defineProperty(navigator, "locks", locks); });

function flow() {
  localStorage.setItem("skein-oidc-generation", "flow-generation");
  sessionStorage.setItem("skein-oidc-flow", JSON.stringify({ verifier: "verifier", state: "state", generation: "flow-generation", returnTo: "/people?tab=notes#top" }));
}

describe("cookie identity coordination without credential fallback", () => {
  it("fails closed before a cookie-changing request when Web Locks are absent", async () => {
    Reflect.deleteProperty(navigator, "locks");
    const auth = await import("@/lib/auth");
    await expect(auth.signInWithKey("sk-skein-test")).rejects.toThrow(/coordinate/);
    expect(calls).toHaveLength(0);
  });

  it.each([429, 503])("keeps identity and never sends an alternate credential during HTTP %s", async (status) => {
    cookie = person();
    localStorage.setItem("skein-key", "sk-skein-other-person");
    vi.stubEnv("NEXT_PUBLIC_API_TOKEN", "shared-token");
    const auth = await import("@/lib/auth"); const api = await import("@/lib/api");
    await auth.bootstrapSession();
    protectedResponse = async () => json({ detail: "Retry later", code: "SESSION_UNAVAILABLE" }, status);
    expect((await api.authenticatedFetch("/api/tasks")).status).toBe(status);
    expect(auth.signedInUser()).toBe("ava");
    expect(JSON.stringify(calls)).not.toMatch(/shared-token|sk-skein-other-person|refresh_token/);
    expect(auth.sessionEnd()).toBe("");
  });

  it("signs out of every browser through the endpoint that ends them all", async () => {
    cookie = person();
    const auth = await import("@/lib/auth");
    await auth.bootstrapSession();
    await auth.signOut(true);
    const ended = calls.filter((c) => c.init?.method === "DELETE");
    expect(ended.map((c) => c.url.replace(/^.*\/api/, "/api"))).toEqual(["/api/auth/sessions"]);
    expect(new Headers(ended[0].init?.headers).get("X-Skein-CSRF")).toBe("csrf-ava");
    expect(auth.isSignedIn()).toBe(false);
    expect(auth.sessionEnd()).toBe("signed-out");
  });

  it("ends the provider session after the local one when the server names where", async () => {
    const assign = vi.fn();
    const real = window.location;
    Object.defineProperty(window, "location", { configurable: true, value: { ...real, assign } });
    try {
      for (const [logoutUrl, expected] of [
        ["https://idp.example.com/logout?id_token_hint=t", "https://idp.example.com/logout?id_token_hint=t"],
        // a javascript: URL would run in this tab
        ["javascript:alert(1)", null],
      ] as const) {
        assign.mockClear();
        cookie = person();
        const auth = await import("@/lib/auth");
        await auth.bootstrapSession();
        const original = vi.mocked(fetch).getMockImplementation()!;
        vi.mocked(fetch).mockImplementation((url, init) =>
          String(url).endsWith("/auth/session") && init?.method === "DELETE"
            ? Promise.resolve(json({ logout_url: logoutUrl }))
            : original(url, init),
        );
        await auth.signOut();
        expect(auth.isSignedIn()).toBe(false);
        if (expected) expect(assign).toHaveBeenCalledWith(expected);
        else expect(assign).not.toHaveBeenCalled();
        vi.mocked(fetch).mockImplementation(original);
      }
    } finally {
      Object.defineProperty(window, "location", { configurable: true, value: real });
    }
  });

  it("does not start an older sign-in whose configuration arrives after logout", async () => {
    vi.stubGlobal("crypto", webcrypto);
    const config = deferred<Response>(); const started = deferred<void>();
    const original = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation((input, init) => {
      if (String(input).endsWith("/auth/config")) { started.resolve(); return config.promise; }
      return original(input, init);
    });
    const auth = await import("@/lib/auth");
    const signingIn = auth.signIn("/people");
    await started.promise;
    await auth.signOut();
    config.resolve(json({ mode: "oidc", error: "", client_id: "skein-web", authorize_url: "https://idp.example.com/authorize" }));
    expect(await signingIn).toMatch(/identity changed/);
    expect(sessionStorage.getItem("skein-oidc-flow")).toBeNull();
    expect(auth.sessionEnd()).toBe("signed-out");
  });

  it("checks the generation before sending a callback that waited behind logout", async () => {
    const auth = await import("@/lib/auth"); flow();
    const released = deferred<void>(); const started = deferred<void>();
    const held = navigator.locks.request("skein-oidc-session", async () => { started.resolve(); await released.promise; });
    await started.promise;
    const completing = auth.completeSignIn("?code=code&state=state");
    const rejected = expect(completing).rejects.toThrow(/identity changed/);
    const logout = auth.signOut();
    released.resolve(); await held; await rejected; await logout;
    expect(calls.some((c) => c.url.endsWith("/auth/token"))).toBe(false);
    expect(auth.isSignedIn()).toBe(false);
  });

  it("holds the lock across Set-Cookie and finishes logout after an in-flight login", async () => {
    const auth = await import("@/lib/auth"); flow();
    const started = deferred<void>(); const finish = deferred<void>();
    exchange = async () => { started.resolve(); await finish.promise; cookie = person(); return json(cookie); };
    const completing = auth.completeSignIn("?code=code&state=state");
    const rejected = expect(completing).rejects.toThrow(/identity changed/);
    await started.promise;
    const logout = auth.signOut();
    await Promise.resolve();
    expect(calls.some((c) => c.init?.method === "DELETE")).toBe(false);
    finish.resolve(); await rejected; await logout;
    const deletion = calls.find((c) => c.init?.method === "DELETE")!;
    expect(new Headers(deletion.init?.headers).get("X-Skein-CSRF")).toBe("csrf-ava");
    expect(cookie.authenticated).toBe(false);
    expect(auth.sessionEnd()).toBe("signed-out");
  });

  it("a newer key sign-in wins over an older callback response", async () => {
    const auth = await import("@/lib/auth"); flow();
    const started = deferred<void>(); const finish = deferred<void>(); let attempt = 0;
    exchange = async () => {
      if (++attempt === 1) { started.resolve(); await finish.promise; cookie = person(); }
      else cookie = person("marcus", "csrf-marcus");
      return json(cookie);
    };
    const old = auth.completeSignIn("?code=code&state=state"); const refused = expect(old).rejects.toThrow(/identity changed/);
    await started.promise;
    const newer = auth.signInWithKey("sk-skein-marcus");
    finish.resolve(); await refused; await newer;
    expect(auth.signedInUser()).toBe("marcus");
    expect(cookie.user).toBe("marcus");
  });

  it.each(["marcus", "ava"])("discards an old GET after a newer %s session", async (user) => {
    cookie = person();
    const auth = await import("@/lib/auth"); const api = await import("@/lib/api");
    await auth.bootstrapSession();
    const finish = deferred<Response>(); const started = deferred<void>();
    protectedResponse = () => { started.resolve(); return finish.promise; };
    const old = api.api("/api/private/notes"); const refused = expect(old).rejects.toThrow(/identity changed/);
    await started.promise;
    exchange = async () => { cookie = person(user, "csrf-new-session"); return json(cookie); };
    await auth.signInWithKey("sk-skein-new-session");
    finish.resolve(json({ notes: "old private notes" })); await refused;
    expect(auth.sessionSnapshot().csrf_token).toBe("csrf-new-session");
  });

  it("does not let an old 401 end a newer same-user session", async () => {
    cookie = person();
    const auth = await import("@/lib/auth"); const api = await import("@/lib/api");
    await auth.bootstrapSession();
    const finish = deferred<Response>(); const started = deferred<void>();
    protectedResponse = () => { started.resolve(); return finish.promise; };
    const pending = api.authenticatedFetch("/api/tasks"); const refused = expect(pending).rejects.toThrow(/identity changed/);
    await started.promise;
    exchange = async () => { cookie = person("ava", "csrf-new"); return json(cookie); };
    await auth.signInWithKey("sk-skein-new");
    finish.resolve(json({ code: "SESSION_INVALID" }, 401)); await refused;
    expect(auth.isSignedIn()).toBe(true);
    expect(auth.sessionSnapshot().csrf_token).toBe("csrf-new");
    expect(calls.filter((c) => c.init?.method === "DELETE")).toHaveLength(0);
  });

  it("updates metadata after an identity mismatch but never replays a mutation", async () => {
    cookie = person();
    const auth = await import("@/lib/auth"); const api = await import("@/lib/api");
    await auth.bootstrapSession();
    protectedResponse = async () => { cookie = person("marcus", "csrf-marcus"); return json({ code: "SESSION_CHANGED", detail: "Session changed" }, 403); };
    await expect(api.api("/api/capture", { method: "POST", body: JSON.stringify({ text: "todo: private intent" }) })).rejects.toThrow("Session changed");
    expect(auth.signedInUser()).toBe("marcus");
    expect(calls.filter((c) => c.url.endsWith("/api/capture"))).toHaveLength(1);
    expect(calls.filter((c) => c.init?.method === "DELETE")).toHaveLength(0);
  });

  it("never falls back to a weak identity when the session cookie disappears", async () => {
    cookie = person(); vi.stubEnv("NEXT_PUBLIC_API_TOKEN", "shared-token");
    const auth = await import("@/lib/auth"); const api = await import("@/lib/api");
    await auth.bootstrapSession();
    protectedResponse = async () => { cookie = anonymous; return json({ code: "SESSION_INVALID" }, 401); };
    await api.authenticatedFetch("/api/tasks");
    expect(auth.isSignedIn()).toBe(false);
    expect(auth.sessionEnd()).toBe("expired");
    expect(api.sessionHeaders()).not.toHaveProperty("Authorization");
  });

  it("joins the callback effect so a code is exchanged once", async () => {
    const auth = await import("@/lib/auth"); flow();
    const first = auth.completeSignIn("?code=code&state=state");
    const second = auth.completeSignIn("?code=code&state=state");
    expect(first).toBe(second);
    expect(await first).toBe("/people?tab=notes#top");
    expect(calls.filter((c) => c.url.endsWith("/auth/token"))).toHaveLength(1);
    expect(sessionStorage.getItem("skein-oidc-flow")).toBeNull();
  });

  it.each(["https://evil.test/", "//evil.test/", "/\t/evil.test/"])("refuses an external callback return path %s", async (returnTo) => {
    const auth = await import("@/lib/auth"); flow();
    const stored = JSON.parse(sessionStorage.getItem("skein-oidc-flow")!);
    sessionStorage.setItem("skein-oidc-flow", JSON.stringify({ ...stored, returnTo }));
    expect(await auth.completeSignIn("?code=code&state=state")).toBe("/");
  });

  it("refuses a wrong state before any token exchange", async () => {
    const auth = await import("@/lib/auth"); flow();
    await expect(auth.completeSignIn("?code=code&state=wrong")).rejects.toThrow(/did not start in this tab/);
    expect(calls).toHaveLength(0);
  });

  it("reports failed logout coordination rather than claiming the server session ended", async () => {
    const auth = await import("@/lib/auth"); cookie = person(); await auth.bootstrapSession();
    vi.spyOn(navigator.locks, "request").mockRejectedValue(new DOMException("Document is not fully active", "InvalidStateError"));
    await expect(auth.signOut()).rejects.toThrow();
    expect(cookie.authenticated).toBe(true);
    expect(auth.sessionEnd()).not.toBe("signed-out");
    expect(calls.some((c) => c.init?.method === "DELETE")).toBe(false);
  });

  it("refuses cookie mutation if its cross-tab generation cannot be stored", async () => {
    const auth = await import("@/lib/auth"); cookie = person(); await auth.bootstrapSession();
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new DOMException("blocked"); });
    await expect(auth.signOut()).rejects.toThrow(/browser storage/);
    expect(cookie.authenticated).toBe(true);
    expect(calls.some((c) => c.init?.method === "DELETE")).toBe(false);
  });

  it("routes uploads and SSE through the shared cookie transport", () => {
    const source = readFileSync(join(__dirname, "..", "app", "runtime-provider.tsx"), "utf8");
    expect(source).toContain('authenticatedFetch("/api/files"');
    expect(source).toContain('authenticatedFetch("/api/chat"');
    expect(source).not.toMatch(/bearer\(|Authorization|userHeader\(/);
  });
});
