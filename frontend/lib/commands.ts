import {
  APPEARANCES,
  COLORWAYS,
  PACKS,
  getColorway,
  nextColorway,
  setAppearance,
  setPack,
} from "./theme";

/** The commands the ⌘K box offers while the reader types (nav-search.tsx).
 *
 *  Derived from the theme arrays, so a new pack or mode is a command the day
 *  it ships. Theme-only on purpose: capture and navigation have their own
 *  doors, and a palette that lists every action is a different feature.
 *  A theme command takes the pack AND its signature accent in one paint,
 *  exactly what the Settings tile does — two doors that disagree about what
 *  "Theme: Ledger" means is the bug this shared setter prevents.
 *
 *  `run` returns the line the status region announces: the page restyling
 *  is the only feedback a sighted reader gets, and a screen reader hears
 *  nothing from a restyle — "Colorway: next" is unusable without it. */
export type Command = { id: string; label: string; run: () => string };

export const COMMANDS: Command[] = [
  ...APPEARANCES.map((a) => ({
    id: `mode-${a.id}`,
    // the Settings control spells the value the same way (3.2.4)
    label: `Mode: ${a.label}`,
    run: () => {
      setAppearance(a.id);
      return `Mode: ${a.label}.`;
    },
  })),
  ...PACKS.map((p) => ({
    id: `pack-${p.id}`,
    label: `Theme: ${p.label}`,
    run: () => {
      setPack(p.id, { accent: true });
      return `Theme: ${p.label}.`;
    },
  })),
  {
    id: "colorway-next",
    label: "Colorway: next",
    run: () => {
      nextColorway();
      const now = COLORWAYS.find((c) => c.id === getColorway());
      return `Colorway: ${now ? now.label : "custom"}.`;
    },
  },
];

/** Every word of the query must start a word of the label, so "dark mode"
 *  finds "Mode: Dark" and "the meeting" finds nothing — a plain substring
 *  match showed seven theme rows for the first four keystrokes of that
 *  search. Under two characters matches nothing: a one-letter query starts
 *  a word in most labels. A `?` or `#` query starts no word, so ask mode and
 *  an exact reference never see a command. */
export function matchCommands(query: string): Command[] {
  const q = query.trim().toLowerCase();
  if (q.length < 2) return [];
  const words = q.split(/\s+/);
  return COMMANDS.filter((c) => {
    const labelWords = c.label.toLowerCase().split(/[\s:]+/);
    return words.every((w) => labelWords.some((lw) => lw.startsWith(w)));
  });
}
