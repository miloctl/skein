/** Date-seeded whimsy: the whole team sees the same line on the same day —
 * shared jokes become team rituals. Deterministic, no LLM. */

function seeded(pool: string[], seedExtra = ""): string {
  // LOCAL date: the shared daily joke must not roll over mid-afternoon
  const d = new Date();
  const seed =
    `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}` + seedExtra;
  let h = 0;
  for (const c of seed) h = (h * 31 + c.charCodeAt(0)) % 100003;
  return pool[h % pool.length];
}

const EMPTY: Record<string, string[]> = {
  review: [
    "Approvals: zero. The agents fear you.",
    "Nothing pending. Approve yourself a coffee.",
    "Empty. The agents have nothing pending — enjoy the quiet.",
  ],
  blockers: [
    "No unresolved blockers. Suspicious. Enjoy it.",
    "Zero blockers. The escalation clock rests.",
    "Nothing is blocked. Someone is about to change that — capture it fast.",
  ],
  allclear: [
    "All clear. Go build something.",
    "Nothing needs you. A rare and beautiful state.",
    "Inbox zero, blocker zero. Frame this moment.",
  ],
};

// Pack-aware voice (TP6): the fabric speaks in its own register on the
// highest-traffic empty states. Capped and fallback-first — a new empty
// state gets the default pool for free; packs never block feature work.
const PACK_EMPTY: Record<string, Record<string, string[]>> = {
  phosphor: {
    review: ["approvals: 0 pending_", "queue empty. exit 0_"],
    blockers: ["no blocked processes_", "blockers: none. uptime holds_"],
    allclear: ["all systems nominal_", "idle loop engaged. nothing needs you_"],
  },
  ledger: {
    review: ["Nothing awaits approval. The ledger is balanced."],
    blockers: ["No blockers on record. The escalation column is blank."],
    allclear: ["Nothing outstanding. The books close clean today."],
  },
  atelier: {
    review: ["Nothing to approve — the gallery is hung."],
    blockers: ["No blockers. The studio is quiet."],
    allclear: ["Nothing needs you. Step back and admire the work."],
  },
};

function currentPack(): string {
  if (typeof document === "undefined") return "loom";
  return document.documentElement.dataset.pack ?? "loom";
}

export function emptyState(view: string): string {
  const packPool = PACK_EMPTY[currentPack()]?.[view];
  return seeded(packPool ?? EMPTY[view] ?? EMPTY.allclear, view);
}

const LOADING = [
  "Consulting the decision log…",
  "Counting blockers (hopefully briefly)…",
  "Waking the Chief of Staff…",
  "Reticulating milestones…",
  "Checking who's blocked on whom…",
];

export function loadingLine(): string {
  return seeded(LOADING, "loading");
}
