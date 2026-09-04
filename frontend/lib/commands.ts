import {
  APPEARANCES,
  PACKS,
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
 *  "Theme: Ledger" means is the bug this shared setter prevents. */
export type Command = { id: string; label: string; run: () => void };

export const COMMANDS: Command[] = [
  ...APPEARANCES.map((a) => ({
    id: `mode-${a.id}`,
    label: `Mode: ${a.label.toLowerCase()}`,
    run: () => setAppearance(a.id),
  })),
  ...PACKS.map((p) => ({
    id: `pack-${p.id}`,
    label: `Theme: ${p.label}`,
    run: () => setPack(p.id, { accent: true }),
  })),
  { id: "colorway-next", label: "Colorway: next", run: () => nextColorway() },
];

/** Substring match over the labels. Under two characters matches nothing:
 *  every label contains a `t`, and seven theme rows over a one-letter query
 *  bury the search the box is for. A `?` or `#` query matches no label, so
 *  ask mode and an exact reference never see a command. */
export function matchCommands(query: string): Command[] {
  const q = query.trim().toLowerCase();
  if (q.length < 2) return [];
  return COMMANDS.filter((c) => c.label.toLowerCase().includes(q));
}
