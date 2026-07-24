/** Date-seeded whimsy: the whole team sees the same line on the same day —
 * shared jokes become team rituals. Deterministic, no LLM. */

function seeded(pool: string[], seedExtra = ""): string {
  const seed = new Date().toISOString().slice(0, 10) + seedExtra;
  let h = 0;
  for (const c of seed) h = (h * 31 + c.charCodeAt(0)) % 100003;
  return pool[h % pool.length];
}

const EMPTY: Record<string, string[]> = {
  review: [
    "Review inbox: zero. The agents fear you.",
    "Nothing pending. Approve yourself a coffee.",
    "Empty. Either the agents are idle or they're flawless. Investigate.",
  ],
  blockers: [
    "No unresolved blockers. Suspicious. Enjoy it.",
    "Zero blockers. The escalation clock rests.",
    "Nothing is blocked. Someone is about to change that — capture it fast.",
  ],
  allclear: [
    "All clear. 🎉 Go build something.",
    "Nothing needs you. A rare and beautiful state.",
    "Inbox zero, blocker zero. Frame this moment.",
  ],
  chat: [
    "The Chief of Staff is listening.",
    "Say the thing. It gets tracked, not lost.",
  ],
};

export function emptyState(view: string): string {
  return seeded(EMPTY[view] ?? EMPTY.allclear, view);
}

const LOADING = [
  "Consulting the decision log…",
  "Counting blockers (hopefully briefly)…",
  "Waking the Chief of Staff…",
  "Reticulating milestones…",
  "Checking who's blocked on whom…",
];

export function loadingLine(): string {
  return seeded(LOADING, String(new Date().getHours()));
}
