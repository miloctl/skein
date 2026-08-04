import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

/** One condition, one wording (CLAUDE.md). 59 surfaces used to interpolate the
 *  raw exception — `alert(String(e))` and `${e.message ?? e}` — which gave the
 *  single condition "the backend is down" about 24 phrasings, several of them
 *  leaking the "Error: " class-name prefix. lib/api.ts owns the wording now.
 *
 *  This pins the RULE, not those 59 sites: the next hand-rolled copy fails
 *  here rather than in front of a user.
 */

const ROOT = join(__dirname, "..");
const DIRS = ["app", "components"];

// `String(e)` and `e.message ?? e` where e is the caught error. Deliberately
// NOT matching String(x.field) — portfolio and dashboard legitimately stringify
// engagement fields named e, and those are values, not errors.
const HANDROLLED = [
  { pattern: /\bString\(\s*(?:e|err|error)\s*\)/, why: "String(e) — use actionError(e) or loadError(e)" },
  { pattern: /\b(?:e|err|error)\.message\s*\?\?/, why: "e.message ?? e — use actionError(e)" },
  {
    pattern: /\binstanceof\s+Error\s*\?\s*\w+\.message/,
    why: "hand-rolled Error narrowing — use actionError(e)",
  },
];

function walk(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) return walk(full);
    return entry.name.endsWith(".tsx") || entry.name.endsWith(".ts") ? [full] : [];
  });
}

const files = DIRS.flatMap((d) => walk(join(ROOT, d)));

describe("the error wording lives in lib/api.ts", () => {
  it("has files to check, so a broken walk cannot pass vacuously", () => {
    expect(files.length).toBeGreaterThan(20);
  });

  it("finds no surface hand-rolling the wording", () => {
    const offenders: string[] = [];
    for (const file of files) {
      readFileSync(file, "utf8")
        .split("\n")
        .forEach((line, i) => {
          for (const { pattern, why } of HANDROLLED) {
            if (pattern.test(line)) {
              offenders.push(`${file.slice(ROOT.length + 1)}:${i + 1}: ${why}\n    ${line.trim()}`);
            }
          }
        });
    }
    expect(offenders).toEqual([]);
  });
});
