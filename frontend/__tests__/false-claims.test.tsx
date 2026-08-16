import { readFileSync } from "node:fs";
import { join } from "node:path";

import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

/** Three surfaces asserted facts they did not have: signIn() answered a
 *  failed CONFIG READ with "this deployment does not use sign-in", Settings
 *  blamed the stored key for a backend that was merely down, and the chat
 *  runtime swallowed a failed history load into an empty conversation. */

afterEach(() => {
  window.localStorage.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("signIn when the config read fails", () => {
  it("reports the read failure, never a verdict about the deployment", async () => {
    vi.stubGlobal("fetch", () => Promise.reject(new TypeError("Failed to fetch")));
    const { signIn } = await import("@/lib/auth");
    const message = await signIn("/");
    expect(message).toContain("Cannot read the sign-in configuration");
    expect(message).not.toContain("does not use sign-in");
  });
});

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: () => Promise.reject(new Error("HTTP 401: key revoked by an administrator")),
    getUser: () => "tester",
  };
});

vi.mock("next/navigation", () => ({
  usePathname: () => "/settings",
}));

import SettingsPage from "@/app/settings/page";

describe("Settings when /api/whoami fails", () => {
  it("shows the failure's own diagnosis, not a guess about the key", async () => {
    window.localStorage.setItem("skein-key", "sk-skein-stored");
    render(<SettingsPage />);
    expect(
      (await screen.findAllByText(/key revoked by an administrator/)).length,
    ).toBeGreaterThan(0);
    expect(screen.queryByText(/possibly revoked/)).toBeNull();
  });
});

describe("the chat runtime's history load", () => {
  it("keeps no swallowed catch — a rejection is a real failure there", () => {
    // the brand-new-thread case resolves [] instead of rejecting, so any
    // .catch(() => {}) in this file is a saved transcript rendered as blank
    const source = readFileSync(
      join(__dirname, "..", "app", "runtime-provider.tsx"),
      "utf8",
    );
    expect(source).not.toMatch(/\.catch\(\(\)\s*=>\s*\{\}\)/);
  });
});
