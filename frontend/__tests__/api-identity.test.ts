import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, userHeader } from "@/lib/api";

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the trusted-header identity", () => {
  it("omits the synthetic anonymous name from requests", async () => {
    const fetch = vi.fn<(_url: string, _init?: RequestInit) => Promise<{ ok: boolean; json: () => Promise<object> }>>()
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
    const fetch = vi.fn<(_url: string, _init?: RequestInit) => Promise<{ ok: boolean; json: () => Promise<object> }>>()
      .mockResolvedValue({ ok: true, json: async () => ({}) });
    vi.stubGlobal("fetch", fetch);

    await api("/api/named-probe", { method: "POST" });

    const headers = fetch.mock.calls[0]?.[1]?.headers as Record<string, string>;
    expect(headers["X-User"]).toBe("mario");
  });
});
