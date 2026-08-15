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
    // both pages POST /api/keys/request; the People page said "Asked" on
    // every click, ignoring already_pending — two behaviors for one
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

  it("says the api-key remedy the way routes/deps.py says it", () => {
    // The auth gate states the same condition the server states in NEED_KEY
    // (api-key mode, no key). It cannot import a Python constant, so the
    // sentences are pinned here instead: deps.py is the source, and a reword
    // there fails this rather than leaving the browser saying the old thing.
    // The gate drops the env-var prefix and the Authorization clause on
    // purpose — a browser reader sets no headers.
    const deps = readFileSync(
      join(ROOT, "..", "backend", "app", "routes", "deps.py"),
      "utf8",
    );
    const gate = readFileSync(join(ROOT, "components", "auth-gate.tsx"), "utf8");
    // as written in Python, which wraps the sentences across string literals
    const shared = [
      "every request needs a personal API key",
      "Get your first one from whoever runs the server",
      "app.bootstrap_key",
      "paste it in Settings, step 2",
    ];
    // Python wraps NEED_KEY across adjacent string literals and JSX wraps it
    // across lines, so both sides are read with the quotes dropped and the
    // whitespace collapsed — otherwise the seam falls inside a sentence.
    const flat = (s: string) => s.replace(/"/g, "").replace(/\s+/g, " ");
    for (const s of shared) {
      expect(flat(deps), `deps.py no longer says: ${s}`).toContain(s);
    }
    for (const s of shared) {
      // the gate capitalizes the opening sentence, so compare case-insensitively
      expect(
        flat(gate).toLowerCase(),
        `auth-gate.tsx has drifted from NEED_KEY: ${s}`,
      ).toContain(s.toLowerCase());
    }
  });
});

describe("the decided lexicon (docs/LEXICON.md)", () => {
  it("says awaiting, not promise to us, for an incoming promise", () => {
    const offenders: string[] = [];
    for (const file of files) {
      readFileSync(file, "utf8")
        .split("\n")
        .forEach((line, i) => {
          if (/promise \(to us\)/i.test(line))
            offenders.push(`${file.slice(ROOT.length + 1)}:${i + 1}: ${line.trim()}`);
        });
    }
    expect(offenders).toEqual([]);
    expect(readFileSync(join(ROOT, "components", "capture-palette.tsx"), "utf8")).toContain(
      '{ prefix: "awaiting:", label: "awaiting" }',
    );
  });

  it("says promise, not commitment, in user-visible text", () => {
    // one concept, one word — promise, end to end (the table, kind, and
    // API path renamed with it). What remains of `commitment` in source is
    // the typed-input alias regexes, which this sweep does not read.
    // "commitment line" is a DIFFERENT concept (tasks committed to an ISO
    // week) and keeps the word.
    const offenders: string[] = [];
    for (const file of files) {
      readFileSync(file, "utf8")
        .split("\n")
        .forEach((line, i) => {
          if (/commitment line/i.test(line)) return;
          if (/^\s*(\/\/|\*|\/\*)/.test(line)) return;
          const shown = [
            ...line.matchAll(/["'`]([^"'`\n]*[Cc]ommitment[^"'`\n]*)["'`]/g),
            ...line.matchAll(/>\s*([^<>{}\n]*[Cc]ommitment[^<>{}\n]*)\s*</g),
          ].map((m) => m[1]);
          for (const s of shown) {
            if (/^[a-z_]+$/.test(s.trim())) continue;
            if (s.includes("/api/")) continue;
            offenders.push(`${file.slice(ROOT.length + 1)}:${i + 1}: ${s.trim()}`);
          }
        });
    }
    expect(offenders).toEqual([]);
  });
});
