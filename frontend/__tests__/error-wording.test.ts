import { describe, expect, it } from "vitest";

import { API_URL, backendUnreachable, isUnreachable, loadError } from "@/lib/api";

/** One condition, one wording (CLAUDE.md). The review found ~24 different
 *  phrasings of "the backend is down", most of them `alert(String(e))`, which
 *  also leaks the "Error: " class-name prefix into user-visible text. These
 *  pin the helpers those surfaces are supposed to call. */

describe("the backend-unreachable wording", () => {
  it("names the URL, because in a self-hosted deployment that is usually the fault", () => {
    expect(backendUnreachable()).toContain(API_URL);
  });

  it("states the fix as an imperative, with no question and no apology", () => {
    const text = backendUnreachable();
    expect(text).toContain("Check that the server is running");
    expect(text).not.toContain("?");
    expect(text.toLowerCase()).not.toContain("sorry");
    expect(text.toLowerCase()).not.toContain("please");
  });

  it("is one string, whatever the underlying error was", () => {
    const a = backendUnreachable(new TypeError("Failed to fetch"));
    const b = backendUnreachable(new TypeError("NetworkError"));
    const prefix = (s: string) => s.slice(0, s.indexOf(" ("));
    expect(prefix(a)).toBe(prefix(b));
    expect(prefix(a)).toBe(backendUnreachable());
  });
});

describe("telling a dead backend from a live refusal", () => {
  it("counts only a transport failure as unreachable", () => {
    // api() throws a plain Error carrying the server's own detail for anything
    // the backend actually answered
    expect(isUnreachable(new TypeError("Failed to fetch"))).toBe(true);
    expect(isUnreachable(new Error("not an active teammate"))).toBe(false);
  });

  it("does not send the reader to check a server that is running and replying", () => {
    const refusal = loadError(new Error("decision #1 already superseded"));
    expect(refusal).not.toContain("Check that the server is running");
    expect(loadError(new TypeError("Failed to fetch"))).toContain(
      "Check that the server is running",
    );
  });
});
