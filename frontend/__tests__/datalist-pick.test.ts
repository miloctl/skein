import { describe, expect, it } from "vitest";

import { isDatalistPick } from "@/lib/datalist-pick";

/** The assign inputs on My Day, Planning and the dashboard commit on Enter
 *  and on a datalist pick. A pick that does not count does nothing, and a
 *  keystroke that counts saves a partial name. */

const change = (inputType?: string) => ({
  nativeEvent: new InputEvent("input", { inputType }),
});

describe("isDatalistPick", () => {
  it("counts a pick as insertReplacementText", () => {
    expect(isDatalistPick(change("insertReplacementText"))).toBe(true);
  });

  it("counts a Firefox pick, which carries no inputType", () => {
    expect(isDatalistPick({ nativeEvent: new Event("input") })).toBe(true);
  });

  it("does not count a typed letter", () => {
    expect(isDatalistPick(change("insertText"))).toBe(false);
  });
});
