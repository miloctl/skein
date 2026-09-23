// The slug-argument half of the composer's autocomplete ("/flock eng"), split
// out as a pure function so a test pins it.

export type ArgItem = { slug: string; emoji?: string; description: string };

/**
 * The roster the composer must offer for this input, and the slug prefix
 * typed so far. Returns null when the input is not a slug argument.
 *
 * The required whitespace after the command name is load-bearing. Without it
 * "/flock" resolves here, the command branch never runs, and the popup jumps
 * straight to the flock roster — "/flocks" disappears from autocomplete for
 * anyone who has typed "/flock" so far (agents/commands.py carries the same
 * pairing note for the backend did-you-mean).
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

/**
 * The `@token` prefix at the caret, its full replacement bounds, and whether
 * it opens the message. Null outside an @token or with text selected.
 *
 * STRICTER than the backend on purpose. services/mentions.py excludes only
 * `[a-z0-9]` before the `@`, so it matches `(@mira`; this needs whitespace.
 * Erring narrow costs a picker that stays shut where a mention would still
 * work; erring wide offers a name inside `root@scout`, which never matches.
 *
 * `atStart` separates the two ways an @slug reaches the bench: a LEADING
 * slug is the deterministic handoff (routes/chat.py rewrites it into the /as
 * form, on every provider), a mid-sentence slug reaches it through the
 * orchestrator's consult tool — which needs a real provider. The picker
 * (components/thread.tsx) decides eligibility from this flag plus the
 * provider; this function only reports the position.
 */
export function mentionQuery(
  text: string,
  caret = text.length,
  selectionEnd = caret,
): { token: string; atStart: boolean; start: number; end: number } | null {
  if (caret !== selectionEnd) return null;
  const hit = /(^|\s)@([a-z0-9._-]*)$/i.exec(text.slice(0, caret));
  if (!hit) return null;
  const start = hit.index + hit[1].length;
  const end = caret + /^[a-z0-9._-]*/i.exec(text.slice(caret))![0].length;
  return {
    token: hit[2].toLowerCase(),
    atStart: text.slice(0, start).trim() === "",
    start,
    end,
  };
}

/**
 * What "/reasoning <level>" offers: every level name the model menu declares,
 * then `default`. The menu is the team menu, not the model of this chat, so
 * the server still refuses a level the chat model does not offer
 * (agents/commands.py `_reasoning`). Empty when no model declares a level.
 */
export function reasoningRoster(menu: { reasoning?: string[] }[]): ArgItem[] {
  const levels = [...new Set(menu.flatMap((m) => m.reasoning ?? []))];
  if (!levels.length) return [];
  return [
    ...levels.map((slug) => ({ slug, description: "" })),
    { slug: "default", description: "Use the team level" },
  ];
}
