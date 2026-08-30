import { readFileSync } from "node:fs";
import { join } from "node:path";

import { afterEach, describe, expect, it, vi } from "vitest";

import { bearer, getApiKey } from "@/lib/api";

/** The credential every request carries, strongest first. Chat rebuilt this
 *  ladder by hand instead of calling bearer(), lost the OIDC rung, and in
 *  oidc mode answered a signed-in user with "sign in" — on the one surface
 *  that does not go through api(). These pin the order itself, so the next
 *  hand-rolled copy fails here rather than in production. */

function signIn(accessToken: string, expiresInMs = 3_600_000) {
  window.localStorage.setItem(
    "skein-oidc",
    JSON.stringify({ access_token: accessToken, expires_at: Date.now() + expiresInMs }),
  );
}

function signInWithExpiredToken() {
  window.localStorage.setItem(
    "skein-oidc",
    JSON.stringify({
      access_token: "expired-ava-token",
      refresh_token: "refresh-ava",
      expires_at: Date.now() - 1_000,
      user: "ava",
    }),
  );
}

function refreshResponse(status: number) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: false,
      status,
      json: async () => ({ detail: "The identity provider did not renew the session." }),
    })),
  );
}

function deferredResponse() {
  let markStarted!: () => void;
  const started = new Promise<void>((resolve) => {
    markStarted = resolve;
  });
  let finish!: (response: Response) => void;
  vi.stubGlobal(
    "fetch",
    vi.fn(() => {
      markStarted();
      return new Promise<Response>((resolve) => {
        finish = resolve;
      });
    }),
  );
  return { started, finish: (response: Response) => finish(response) };
}

const originalLocks = Object.getOwnPropertyDescriptor(navigator, "locks");

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  if (originalLocks) Object.defineProperty(navigator, "locks", originalLocks);
  else Reflect.deleteProperty(navigator, "locks");
});

describe("the credential ladder", () => {
  it("prefers a signed-in OIDC session over a personal key", async () => {
    signIn("oidc-token");
    window.localStorage.setItem("skein-key", "sk-skein-personal");
    expect(await bearer()).toBe("oidc-token");
  });

  it("fails closed when the browser has no cross-tab lock", async () => {
    Reflect.deleteProperty(navigator, "locks");
    window.localStorage.setItem(
      "skein-oidc",
      JSON.stringify({
        access_token: "expired-ava-token",
        refresh_token: "refresh-ava",
        expires_at: Date.now() - 1_000,
        user: "ava",
      }),
    );
    window.localStorage.setItem("skein-key", "sk-skein-different-user");
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);

    expect(await bearer()).toBe("");
    expect(fetch).not.toHaveBeenCalled();
    expect(window.localStorage.getItem("skein-oidc")).not.toBeNull();
  });

  it("does not change identity when an OIDC refresh is temporarily unavailable", async () => {
    signInWithExpiredToken();
    window.localStorage.setItem("skein-key", "sk-skein-different-user");
    vi.stubEnv("NEXT_PUBLIC_API_TOKEN", "shared-token");
    refreshResponse(503);

    expect(await bearer()).toBe("");
    expect(window.localStorage.getItem("skein-oidc")).not.toBeNull();
    expect(getApiKey()).toBe("sk-skein-different-user");
  });

  it("does not use a personal key when OIDC refresh is rate-limited", async () => {
    signInWithExpiredToken();
    window.localStorage.setItem("skein-key", "sk-skein-different-user");
    refreshResponse(429);

    expect(await bearer()).toBe("");
    expect(window.localStorage.getItem("skein-oidc")).not.toBeNull();
  });

  it("does not use the shared token when OIDC refresh is rate-limited", async () => {
    signInWithExpiredToken();
    vi.stubEnv("NEXT_PUBLIC_API_TOKEN", "shared-token");
    refreshResponse(429);

    expect(await bearer()).toBe("");
    expect(window.localStorage.getItem("skein-oidc")).not.toBeNull();
  });

  it("does not switch identity while waiting for the cross-tab refresh lock", async () => {
    signInWithExpiredToken();
    let release!: () => void;
    const held = navigator.locks.request(
      "skein-oidc-refresh",
      () =>
        new Promise<void>((resolve) => {
          release = resolve;
        }),
    );
    await Promise.resolve();
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);

    const pending = bearer();
    signIn("bob-token");
    const bob = JSON.parse(window.localStorage.getItem("skein-oidc") ?? "null");
    bob.user = "bob";
    window.localStorage.setItem("skein-oidc", JSON.stringify(bob));
    release();
    await held;

    expect(await pending).toBe("");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("does not overwrite a different OIDC identity with a stale refresh", async () => {
    signInWithExpiredToken();
    const refresh = deferredResponse();

    const pending = bearer();
    await refresh.started;
    signIn("bob-token");
    const bob = JSON.parse(window.localStorage.getItem("skein-oidc") ?? "null");
    bob.user = "bob";
    window.localStorage.setItem("skein-oidc", JSON.stringify(bob));
    refresh.finish(
      new Response(
        JSON.stringify({
          access_token: "renewed-ava-token",
          refresh_token: "renewed-ava-refresh",
          expires_in: 3_600,
          user: "ava",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    expect(await pending).toBe("");
    expect(JSON.parse(window.localStorage.getItem("skein-oidc") ?? "null").user).toBe(
      "bob",
    );
  });

  it("does not overwrite a newer session for the same OIDC user", async () => {
    signInWithExpiredToken();
    const refresh = deferredResponse();

    const pending = bearer();
    await refresh.started;
    window.localStorage.setItem(
      "skein-oidc",
      JSON.stringify({
        access_token: "newer-ava-token",
        refresh_token: "newer-ava-refresh",
        expires_at: Date.now() + 3_600_000,
        user: "ava",
      }),
    );
    refresh.finish(
      new Response(
        JSON.stringify({
          access_token: "stale-ava-token",
          refresh_token: "stale-ava-refresh",
          expires_in: 3_600,
          user: "ava",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    expect(await pending).toBe("");
    expect(
      JSON.parse(window.localStorage.getItem("skein-oidc") ?? "null").access_token,
    ).toBe("newer-ava-token");
  });

  it("does not delete a newer same-user session after a stale rejection", async () => {
    signInWithExpiredToken();
    const refresh = deferredResponse();

    const pending = bearer();
    await refresh.started;
    window.localStorage.setItem(
      "skein-oidc",
      JSON.stringify({
        access_token: "newer-ava-token",
        refresh_token: "newer-ava-refresh",
        expires_at: Date.now() + 3_600_000,
        user: "ava",
      }),
    );
    refresh.finish(
      new Response(JSON.stringify({ detail: "The identity provider refused it." }), {
        status: 400,
        headers: { "Content-Type": "application/json" },
      }),
    );

    expect(await pending).toBe("");
    expect(
      JSON.parse(window.localStorage.getItem("skein-oidc") ?? "null").access_token,
    ).toBe("newer-ava-token");
  });

  it("does not restore OIDC or use a key after sign-out during refresh", async () => {
    signInWithExpiredToken();
    window.localStorage.setItem("skein-key", "sk-skein-different-user");
    const refresh = deferredResponse();

    const pending = bearer();
    await refresh.started;
    window.localStorage.removeItem("skein-oidc");
    refresh.finish(
      new Response(
        JSON.stringify({
          access_token: "renewed-ava-token",
          refresh_token: "renewed-ava-refresh",
          expires_in: 3_600,
          user: "ava",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    expect(await pending).toBe("");
    expect(window.localStorage.getItem("skein-oidc")).toBeNull();
  });

  it("invalidates refresh when sign-out lands at the final state check", async () => {
    signInWithExpiredToken();
    const auth = await import("@/lib/auth");
    let markStarted!: () => void;
    const refreshStarted = new Promise<void>((resolve) => {
      markStarted = resolve;
    });
    let finish!: (response: Response) => void;
    vi.stubGlobal(
      "fetch",
      vi.fn(() => {
        markStarted();
        return new Promise<Response>((resolve) => {
          finish = resolve;
        });
      }),
    );
    const originalSetItem = Storage.prototype.setItem;
    let signedOut: Promise<void> | null = null;
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (this: Storage, key, value) {
      if (key === "skein-oidc" && value.includes("renewed-ava-token"))
        signedOut = auth.signOut();
      return originalSetItem.call(this, key, value);
    });

    const pending = bearer();
    await refreshStarted;
    finish(
      new Response(
        JSON.stringify({
          access_token: "renewed-ava-token",
          refresh_token: "renewed-ava-refresh",
          expires_in: 3_600,
          user: "ava",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    expect(await pending).toBe("");
    expect(signedOut).not.toBeNull();
    await signedOut;
    expect(window.localStorage.getItem("skein-oidc")).toBeNull();
    expect(window.sessionStorage.getItem("skein-oidc-ended")).toBe("signed-out");
  });

  it("does not complete an older sign-in after sign-out", async () => {
    const auth = await import("@/lib/auth");
    window.localStorage.setItem("skein-oidc-generation", "ava-flow");
    window.sessionStorage.setItem(
      "skein-oidc-flow",
      JSON.stringify({
        verifier: "verifier",
        state: "ava-state",
        generation: "ava-flow",
        returnTo: "/",
      }),
    );
    let finish!: (response: Response) => void;
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          new Promise<Response>((resolve) => {
            finish = resolve;
          }),
      ),
    );

    const pending = auth.completeSignIn("?code=ava-code&state=ava-state");
    const signedOut = auth.signOut();
    finish(
      new Response(
        JSON.stringify({
          access_token: "ava-token",
          refresh_token: "ava-refresh",
          expires_in: 3_600,
          user: "ava",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    await expect(pending).rejects.toThrow("Another identity change");
    await signedOut;
    expect(window.localStorage.getItem("skein-oidc")).toBeNull();
  });

  it("does not overwrite a newer identity with an older sign-in", async () => {
    const auth = await import("@/lib/auth");
    window.localStorage.setItem("skein-oidc-generation", "ava-flow-2");
    window.sessionStorage.setItem(
      "skein-oidc-flow",
      JSON.stringify({
        verifier: "verifier",
        state: "ava-state-2",
        generation: "ava-flow-2",
        returnTo: "/",
      }),
    );
    let finish!: (response: Response) => void;
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          new Promise<Response>((resolve) => {
            finish = resolve;
          }),
      ),
    );

    const pending = auth.completeSignIn("?code=ava-code-2&state=ava-state-2");
    window.localStorage.setItem("skein-oidc-generation", "bob-flow");
    window.localStorage.setItem(
      "skein-oidc",
      JSON.stringify({
        access_token: "bob-token",
        refresh_token: "bob-refresh",
        expires_at: Date.now() + 3_600_000,
        user: "bob",
        generation: "bob-flow",
      }),
    );
    finish(
      new Response(
        JSON.stringify({
          access_token: "ava-token",
          refresh_token: "ava-refresh",
          expires_in: 3_600,
          user: "ava",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    await expect(pending).rejects.toThrow("Another identity change");
    expect(JSON.parse(window.localStorage.getItem("skein-oidc") ?? "null").user).toBe(
      "bob",
    );
  });

  it("uses the personal key after the identity provider rejects the OIDC session", async () => {
    signInWithExpiredToken();
    window.localStorage.setItem("skein-key", "sk-skein-personal");
    refreshResponse(400);

    expect(await bearer()).toBe("sk-skein-personal");
    expect(window.localStorage.getItem("skein-oidc")).toBeNull();
  });

  it("uses the shared token after the identity provider rejects the OIDC session", async () => {
    signInWithExpiredToken();
    vi.stubEnv("NEXT_PUBLIC_API_TOKEN", "shared-token");
    refreshResponse(400);

    expect(await bearer()).toBe("shared-token");
    expect(window.localStorage.getItem("skein-oidc")).toBeNull();
  });

  it("falls back to the personal key when nobody is signed in", async () => {
    window.localStorage.setItem("skein-key", "sk-skein-personal");
    expect(await bearer()).toBe("sk-skein-personal");
  });

  it("is empty with no session and no key, rather than sending a bare header", async () => {
    expect(await bearer()).toBe("");
    expect(getApiKey()).toBe("");
  });

  it("drops an expired session that cannot be renewed", async () => {
    // expired and no refresh_token: holding it would render a signed-in UI
    // that 401s on every request
    signIn("stale-token", -1_000);
    expect(await bearer()).toBe("");
  });

  it("is what the chat runtime actually calls", () => {
    // the ladder tests above prove bearer(); they prove nothing about the one
    // surface that bypasses api() — a hand-rolled copy there loses the OIDC
    // rung while every test above stays green.
    const source = readFileSync(join(__dirname, "..", "app", "runtime-provider.tsx"), "utf8");
    expect(source).toContain("await bearer()");
    expect(source).toContain("...userHeader()");
    expect(source).not.toContain("getApiKey() ||");
    expect(source).not.toContain('"X-User": getUser()');
  });
});
