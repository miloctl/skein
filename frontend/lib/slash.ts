// The slug-argument half of the composer's autocomplete ("/flock eng"), split
// out as a pure function so a test pins it.

export type ArgItem = { slug: string; emoji: string; description: string };

/**
 * The roster the composer must offer for this input, and the slug prefix
 * typed so far. Returns null when the input is not a slug argument.
 *
 * The required whitespace after the command name is load-bearing. Without it
 * "/flock" resolves here with an empty prefix, so it leaves the command
 * branch that sorts an exact match first — and Tab then completes "/flocks"
 * for someone who typed "/flock" in full (agents/commands.py carries the same
 * note for the backend did-you-mean).
 */
export function argQuery(
  text: string,
  rosterNames: string[],
): { cmd: string; token: string } | null {
  const hit = /^\/([a-z]+)\s+([a-z0-9-]*)$/i.exec(text);
  if (!hit) return null;
  const cmd = hit[1].toLowerCase();
  if (!rosterNames.includes(cmd)) return null;
  return { cmd, token: hit[2].toLowerCase() };
}
