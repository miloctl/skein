import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

/** A role=status node is mounted before its text, so a screen reader hears
 *  the text arrive (components/status-region.tsx). empty:hidden is
 *  display:none while the node is empty, which takes it out of the
 *  accessibility tree: the region and its first text then appear together,
 *  and the text is often not announced. */

const ROOT = join(__dirname, "..");
const DIRS = ["app", "components"];

function walk(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) return walk(full);
    return entry.name.endsWith(".tsx") ? [full] : [];
  });
}

describe("a status region", () => {
  it("does not hide itself while it is empty", () => {
    const offenders: string[] = [];
    let checked = 0;
    for (const file of DIRS.flatMap((d) => walk(join(ROOT, d)))) {
      for (const tag of readFileSync(file, "utf8").match(/<\w+\b[^>]*>/g) ?? []) {
        if (!tag.includes('role="status"')) continue;
        checked += 1;
        if (/\bempty:hidden\b/.test(tag)) {
          offenders.push(`${file.slice(ROOT.length + 1)}: ${tag.replace(/\s+/g, " ")}`);
        }
      }
    }
    // a broken walk finds nothing and passes vacuously
    expect(checked).toBeGreaterThan(5);
    expect(offenders).toEqual([]);
  });
});
