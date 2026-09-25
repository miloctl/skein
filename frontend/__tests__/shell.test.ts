import { describe, expect, it } from "vitest";

import { shellQuote } from "@/lib/shell";

describe("shellQuote", () => {
  it("keeps a plain or email-shaped name bare, as shlex.quote does", () => {
    expect(shellQuote("ava.lee+ops@example.com")).toBe("ava.lee+ops@example.com");
  });

  it("quotes a name the shell would split or expand", () => {
    expect(shellQuote("ava lee")).toBe("'ava lee'");
    expect(shellQuote("@true")).toBe("'@true'");
    expect(shellQuote("o'brien")).toBe(`'o'"'"'brien'`);
  });
});
