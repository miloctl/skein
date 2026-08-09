/** Date-seeded whimsy: the whole team sees the same line on the same day —
 * shared jokes become team rituals. Deterministic, no LLM. */

function seeded(pool: string[], seedExtra = ""): string {
  // The DATE is a client fact, exactly like the pack below. The server has a
  // different clock (and, once a page is statically prerendered, a clock
  // frozen at BUILD time — "Reticulating milestones" was baked into
  // index.html and served on every later day). Rendering a date-seeded line
  // during SSR or the hydration pass therefore mismatches, React regenerates
  // from the root, and that recreates <html> and throws away the theme the
  // pre-paint script set. Same failure the pack gate exists to stop, so it
  // takes the same gate: one fixed line until the client is in charge.
  if (!hydrated) return pool[0];
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
    review: ["approvals: 0 pending. exit 0", "queue empty. nothing to sign off", "0 proposals awaiting verdict"],
    blockers: ["no blocked processes", "blockers: none. uptime holds", "escalation daemon: idle"],
    allclear: ["all systems nominal", "idle loop engaged. nothing needs you", "inbox 0. load average 0.00"],
  },
  ledger: {
    review: ["Nothing awaits approval. The ledger is balanced.", "No entries pending. The columns reconcile.", "Approvals: nil. Carried forward: nothing."],
    blockers: ["No blockers on record. The escalation column is blank.", "Obstructions: none filed.", "The blocker register shows a clean page."],
    allclear: ["Nothing outstanding. The books close clean today.", "All accounts settled. Go to press.", "No items carried over. A tidy edition."],
  },
  atelier: {
    review: ["Nothing to approve — the gallery is hung.", "No proposals on the easel.", "The review wall is bare, beautifully."],
    blockers: ["No blockers. The studio is quiet.", "Nothing in the way of the work.", "Every piece has room to breathe."],
    allclear: ["Nothing needs you. Step back and admire the work.", "The studio is swept. Make something.", "A blank canvas kind of day."],
  },
  claw: {
    review: ["Approvals: clear. The deck is quiet.", "Nothing staged for sign-off.", "Zero pending. All hands free."],
    blockers: ["No blockers on the board.", "Nothing jammed. Systems green.", "Blocker count: zero. Hold course."],
    allclear: ["Deck clear. Take the helm elsewhere.", "Nothing needs you. The console rests.", "All quiet across the deck."],
  },
  hermes: {
    review: ["Telemetry clean — nothing awaits approval.", "Approval queue empty. Instruments steady.", "No signals pending sign-off."],
    blockers: ["No blockers on the wire.", "All channels open. Nothing is stuck.", "Obstruction scan returned nothing."],
    allclear: ["Instruments read nominal. Go make contact.", "Mission board clear. Nothing needs you.", "Quiet skies. Enjoy the glide."],
  },
};

// SSR and the hydration pass must render the same text: the pack voice only
// speaks after hydration (callers re-render when their data loads, which is
// when empty states actually matter).
//
// The flag flips in a post-hydration EFFECT, never a microtask. A microtask
// fires when the module finishes evaluating — long before React hydrates — so
// the pack voice raced into the hydration render itself. That mismatch made
// React regenerate the tree from the root, which recreates <html> and throws
// away the data-pack/data-theme/data-appearance the pre-paint script set:
// picking a non-default pack silently reverted your theme on any page that
// rendered an empty state. See ThemeSync, which calls markHydrated().
let hydrated = false;

export function markHydrated(): void {
  hydrated = true;
}

function currentPack(): string {
  if (typeof document === "undefined" || !hydrated) return "loom";
  return document.documentElement.dataset.pack ?? "loom";
}

export function emptyState(view: string): string {
  const packPool = PACK_EMPTY[currentPack()]?.[view];
  return seeded(packPool ?? EMPTY[view] ?? EMPTY.allclear, view);
}

// The sign-off, shown by the auth gate after a CHOSEN sign-out — the person
// is done and nothing is asked of them, which is where warmth is allowed.
// Every line starts with "Signed out." so the state is stated before the
// voice starts (auth-gate.test.tsx keys on that prefix). The expired-session
// wording never comes from here: that state asks the reader to act.
const SIGNED_OUT = [
  "Signed out. The formation flies on.",
  "Signed out. Your strand rests — the weave holds.",
  "Signed out. The loom hums along without you, a little quieter.",
];

export function signedOutLine(): string {
  return seeded(SIGNED_OUT, "signed-out");
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
