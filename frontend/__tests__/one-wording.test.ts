import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

/** Two structural rules with the same shape: a surface must not invent its
 *  own copy of something the design owns elsewhere. Source-level sweeps, like
 *  no-handrolled-errors.test.ts — they pin the rule, not today's sites. */

const ROOT = join(__dirname, "..");

function walk(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) return walk(full);
    return entry.name.endsWith(".tsx") || entry.name.endsWith(".ts") ? [full] : [];
  });
}

const files = ["app", "components", "lib"].flatMap((d) => walk(join(ROOT, d)));

describe("colors come from the theme, not the Tailwind palette", () => {
  it("finds no raw palette class outside lib/theme.ts", () => {
    // every theme pack re-proves its tokens against the contrast gate;
    // a raw text-green-600 is the one color proved against no pack. Found
    // live: the agents page's health glyph, invisible under phosphor.
    const RAW =
      /\b(?:text|bg|border|ring|fill|stroke|from|to|via)-(?:red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose|slate|gray|zinc|neutral|stone)-\d{2,3}\b/;
    const offenders: string[] = [];
    for (const file of files) {
      if (file.endsWith("lib/theme.ts")) continue; // the palette's one legitimate home
      readFileSync(file, "utf8")
        .split("\n")
        .forEach((line, i) => {
          if (RAW.test(line)) offenders.push(`${file.slice(ROOT.length + 1)}:${i + 1}: ${line.trim()}`);
        });
    }
    expect(offenders).toEqual([]);
  });
});

describe("one condition, one wording across surfaces", () => {
  it("keeps the key-request reply identical on Settings and 1:1s", () => {
    // both pages POST /api/keys/request; the People page once said "Asked"
    // on every click, ignoring already_pending — two behaviors for one
    // condition, then two wordings for one outcome
    const strings = [
      "Already asked — the request is still on the team's My Day.",
      "Asked — the request (with the exact command) is now on the team's My Day.",
    ];
    for (const page of ["app/settings/page.tsx", "app/people/page.tsx"]) {
      const source = readFileSync(join(ROOT, page), "utf8");
      for (const s of strings) {
        expect(source, `${page} is missing: ${s}`).toContain(s);
      }
    }
  });
});
