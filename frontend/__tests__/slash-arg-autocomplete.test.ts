import { describe, expect, it } from "vitest";

import { argQuery, mentionQuery, reasoningRoster } from "@/lib/slash";

const ROSTERS = ["as", "flock"];

describe("argQuery", () => {
  it("offers the roster once a command has whitespace after it", () => {
    expect(argQuery("/flock ", ROSTERS)).toEqual({ cmd: "flock", token: "" });
    expect(argQuery("/flock eng", ROSTERS)).toEqual({
      cmd: "flock",
      token: "eng",
    });
    expect(argQuery("/as gr", ROSTERS)).toEqual({ cmd: "as", token: "gr" });
  });

  it("leaves a bare command to the command popup", () => {
    // the /flock-vs-/flocks hazard: if "/flock" resolved here the command
    // branch never runs, and "/flocks" disappears from autocomplete for
    // anyone who has typed "/flock" so far
    expect(argQuery("/flock", ROSTERS)).toBeNull();
    expect(argQuery("/flocks", ROSTERS)).toBeNull();
    expect(argQuery("/as", ROSTERS)).toBeNull();
  });

  it("closes once the slug is complete, so the popup leaves the message alone", () => {
    // what run() and Tab both write is "/flock engineering " — the trailing
    // space must NOT reopen the roster with an empty prefix
    expect(argQuery("/flock engineering ", ROSTERS)).toBeNull();
    expect(
      argQuery("/flock engineering what breaks first", ROSTERS),
    ).toBeNull();
  });

  it("ignores a command that takes no slug", () => {
    expect(argQuery("/plan onboarding", ROSTERS)).toBeNull();
    expect(argQuery("/search flock", ROSTERS)).toBeNull();
  });

  it("matches the command and the slug without regard to case", () => {
    expect(argQuery("/FLOCK Eng", ROSTERS)).toEqual({
      cmd: "flock",
      token: "eng",
    });
  });
});

describe("mentionQuery", () => {
  it("opens on an @token being typed", () => {
    expect(mentionQuery("@")).toEqual({ token: "", atStart: true, start: 0, end: 1 });
    expect(mentionQuery("@mi")).toEqual({ token: "mi", atStart: true, start: 0, end: 3 });
    expect(mentionQuery("can @mi")).toEqual({ token: "mi", atStart: false, start: 4, end: 7 });
  });

  it("finds the token at the caret before existing text", () => {
    expect(mentionQuery("@bac plan it", 4)).toEqual({
      token: "bac", atStart: true, start: 0, end: 4,
    });
  });

  it("includes the rest of a partially edited token in the replacement bounds", () => {
    expect(mentionQuery("ask @bac-old plan it", 8)).toEqual({
      token: "bac", atStart: false, start: 4, end: 12,
    });
  });

  it("does not complete selected text or a caret outside a mention", () => {
    expect(mentionQuery("@bac", 1, 4)).toBeNull();
    expect(mentionQuery("@bac plan it", 0)).toBeNull();
    expect(mentionQuery("@bac plan it", 5)).toBeNull();
    expect(mentionQuery("mail ava@example today", 12)).toBeNull();
    expect(mentionQuery("(@bac plan it", 5)).toBeNull();
  });

  it("marks only a leading @ as atStart, because only that invokes", () => {
    // routes/chat.py strips the message before testing startswith("@"), so
    // leading whitespace still invokes and the picker must agree
    expect(mentionQuery("   @grow")?.atStart).toBe(true);
    expect(mentionQuery("ask @grow")?.atStart).toBe(false);
  });

  it("is not an ssh target or an email localpart", () => {
    // services/mentions.py refuses these too — offering a name here would
    // suggest a mention the backend never matches
    expect(mentionQuery("run ssh root@scout")).toBeNull();
    expect(mentionQuery("mail ava@example")).toBeNull();
  });

  it("closes once the name is complete", () => {
    expect(mentionQuery("@mira ")).toBeNull();
    expect(mentionQuery("@mira please look")).toBeNull();
    expect(mentionQuery("no mention here")).toBeNull();
  });
});

describe("reasoningRoster", () => {
  it("offers each level the menu declares once, then default", () => {
    expect(
      reasoningRoster([
        { reasoning: ["low", "high"] },
        { reasoning: [] },
        {},
        { reasoning: ["off", "high"] },
      ]).map((item) => item.slug),
    ).toEqual(["low", "high", "off", "default"]);
  });

  it("offers nothing when no model declares a level", () => {
    expect(reasoningRoster([{ reasoning: [] }, {}])).toEqual([]);
  });
});
