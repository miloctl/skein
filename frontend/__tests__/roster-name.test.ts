import { describe, expect, it } from "vitest";
import { rosterNameProblem } from "@/lib/api";

// routes/deps.py refuses an X-User outside the @mention charset on every
// request, so a name the picker saved anyway locked the browser out of the API
describe("rosterNameProblem", () => {
  it("accepts one mention token", () => {
    for (const ok of ["ava", "Ava", "ava.b-c_1", "x", "a".repeat(64)]) expect(rosterNameProblem(ok)).toBe("");
  });
  it("refuses what the API refuses, with the rule", () => {
    for (const bad of ["../etc", "a b", "ava;drop", "-ava", ".hidden", "ava@corp"]) expect(rosterNameProblem(bad)).toMatch(/letters, digits/);
    expect(rosterNameProblem("   ")).toBe("Type a name.");
    expect(rosterNameProblem("a".repeat(65))).toMatch(/64/);
  });
});
