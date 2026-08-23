import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, authenticatedFetch, userHeader } from "@/lib/api";

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the trusted-header identity", () => {
  it("omits the synthetic anonymous name from requests", async () => {
    const fetch = vi
      .fn<
        (
          _url: string,
          _init?: RequestInit,
        ) => Promise<{ ok: boolean; json: () => Promise<object> }>
      >()
      .mockResolvedValue({ ok: true, json: async () => ({}) });
    vi.stubGlobal("fetch", fetch);

    await api("/api/anonymous-probe", { method: "POST" });

    const headers = fetch.mock.calls[0]?.[1]?.headers as Record<string, string>;
    expect(headers).not.toHaveProperty("X-User");
  });

  it("uses the same anonymous omission for raw request paths", () => {
    expect(userHeader()).toEqual({});
  });

  it("sends a name after the person picks one", async () => {
    window.localStorage.setItem("skein-user", "mario");
    expect(userHeader()).toEqual({ "X-User": "mario" });
    const fetch = vi
      .fn<
        (
          _url: string,
          _init?: RequestInit,
        ) => Promise<{ ok: boolean; json: () => Promise<object> }>
      >()
      .mockResolvedValue({ ok: true, json: async () => ({}) });
    vi.stubGlobal("fetch", fetch);

    await api("/api/named-probe", { method: "POST" });

    const headers = fetch.mock.calls[0]?.[1]?.headers as Record<string, string>;
    expect(headers["X-User"]).toBe("mario");
  });

  it("authenticates raw file responses through the shared request path", async () => {
    window.localStorage.setItem("skein-user", "mario");
    window.localStorage.setItem("skein-key", "personal-key");
    const fetch = vi
      .fn()
      .mockResolvedValue(new Response("file", { status: 200 }));
    vi.stubGlobal("fetch", fetch);

    await authenticatedFetch("/api/file");

    const headers = fetch.mock.calls[0]?.[1]?.headers as Record<string, string>;
    expect(headers).toMatchObject({
      "X-User": "mario",
      "X-Client": "web",
      Authorization: "Bearer personal-key",
    });
  });

  it("demotes a stored OIDC token when a raw response rejects it", async () => {
    const future = Date.now() + 10 * 60_000;
    window.localStorage.setItem(
      "skein-oidc",
      JSON.stringify({
        access_token: "oidc-token",
        refresh_token: "refresh-token",
        expires_at: future,
      }),
    );
    const fetch = vi
      .fn()
      .mockResolvedValue(new Response("", { status: 401 }));
    vi.stubGlobal("fetch", fetch);

    await authenticatedFetch("/api/file");

    const headers = new Headers(fetch.mock.calls[0]?.[1]?.headers);
    expect(headers.get("Authorization")).toBe("Bearer oidc-token");
    const stored = JSON.parse(
      window.localStorage.getItem("skein-oidc") ?? "{}",
    );
    expect(stored.access_token).toBe("oidc-token");
    expect(stored.refresh_token).toBe("refresh-token");
    expect(stored.expires_at).not.toBe(future);
    expect(stored.expires_at).toBeGreaterThan(0);
    expect(stored.expires_at).toBeLessThan(Date.now());
  });
});
