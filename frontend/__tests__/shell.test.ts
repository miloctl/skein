import { describe, expect, it } from "vitest";

import { commandName } from "@/lib/shell";

describe("commandName", () => {
  it("prints a name the backend accepts the way shlex.quote does", () => {
    expect(commandName("ava.lee+ops@example.com")).toBe("ava.lee+ops@example.com");
    expect(commandName("ava lee")).toBe("'ava lee'");
    expect(commandName("ava", "-mcp")).toBe("ava-mcp");
  });

  it("refuses a name no quoting keeps safe in PowerShell", () => {
    // a quote inside the name split it in PowerShell and ran what followed
    for (const name of ["o'brien", "x’;echo INJECTED;’", 'a"b', "@true", "-h"]) {
      expect(commandName(name)).toBeNull();
    }
  });
});
