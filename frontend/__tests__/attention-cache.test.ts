/** An attention change relayed from another tab (lib/attention.ts) must reach
 *  the server. The write happened in the other tab, so this tab's GET cache
 *  still holds the old count, and a refresh served from it moves no badge. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let reads = 0;
beforeEach(() => {
  vi.resetModules();
  reads = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.endsWith("/auth/config")) return new Response(JSON.stringify({ mode: "trusted-header", error: "" }));
    if (url.endsWith("/auth/session"))
      return new Response(JSON.stringify({ authenticated: false, user: "anonymous", strong: false, auth_method: "", csrf_token: "" }));
    reads += 1;
    return new Response(JSON.stringify({ inbox: reads, yours: 0 }));
  }));
});
afterEach(() => vi.unstubAllGlobals());

describe("the GET cache and an attention change", () => {
  it("rereads the attention count after the change event", async () => {
    const { api } = await import("@/lib/api");
    // the first request bootstraps the session, whose storage event clears
    // the cache that request just filled
    await api("/api/attention");
    const cached = await api("/api/attention");
    expect(await api("/api/attention")).toEqual(cached);

    window.dispatchEvent(new Event("skein-attention-change"));
    expect(await api("/api/attention")).not.toEqual(cached);
  });
});
