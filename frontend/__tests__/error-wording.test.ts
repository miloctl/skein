import { describe, expect, it } from "vitest";

import { actionError, API_URL, backendUnreachable, isUnreachable, loadError } from "@/lib/api";

/** One condition, one wording (CLAUDE.md). The single condition "the backend
 *  is down" shipped in ~24 phrasings, most of them `alert(String(e))`, which
 *  also leaks the "Error: " class-name prefix into user-visible text. These
 *  pin the helpers those surfaces call. */

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

describe("the 'Error: ' class-name prefix", () => {
  it("never reaches the reader, from either helper", () => {
    // String(new Error("x")) is "Error: x". Every surface that interpolated an
    // error showed that prefix — JS internals, and nothing to act on.
    const refusal = new Error("decision #1 is already superseded");
    expect(actionError(refusal)).toBe("decision #1 is already superseded");
    expect(loadError(refusal)).not.toContain("Error:");
    expect(backendUnreachable(new TypeError("Failed to fetch"))).not.toContain("TypeError:");
  });

  it("still carries a non-Error rejection through readably", () => {
    expect(actionError("plain string reason")).toBe("plain string reason");
  });
});

describe("a failed action versus a failed page load", () => {
  it("lets the server's own sentence stand on its own", () => {
    // the backend writes these for this reader; "Could not load this page"
    // would name the wrong thing after a button press
    expect(actionError(new Error("person is not an active teammate"))).toBe(
      "person is not an active teammate",
    );
    expect(loadError(new Error("nope"))).toContain("Could not load this page");
  });

  it("routes an unreachable backend to the one wording, from both", () => {
    const dead = new TypeError("Failed to fetch");
    expect(actionError(dead)).toContain("Check that the server is running");
    expect(loadError(dead)).toContain("Check that the server is running");
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
