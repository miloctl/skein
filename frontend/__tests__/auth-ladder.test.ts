import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

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

describe("the credential ladder", () => {
  it("prefers a signed-in OIDC session over a personal key", async () => {
    signIn("oidc-token");
    window.localStorage.setItem("skein-key", "sk-skein-personal");
    expect(await bearer()).toBe("oidc-token");
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
