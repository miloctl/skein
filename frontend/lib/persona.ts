// Sticky persona state for the chat thread. The chip in the composer and
// the runtime adapter both read it: once a persona is active, freeform
// messages are invisibly prefixed with "/as <slug> " so the backend contract
// (and per-persona sessions, identity, masthead) is unchanged. Slash
// commands are never prefixed — they stay deterministic and thread-free.

export type Persona = {
  slug: string;
  name: string;
  description: string;
  emoji: string;
  vibe?: string;
};

// persona is per-conversation state, and the thread id survives reloads
// (sessionStorage) — so the persona must too, or a refresh silently drops
// you back to the Chief of Staff mid-conversation with no notice
const STICKY_KEY = "skein-chat-persona";

function restore(): Persona | null {
  try {
    const raw = sessionStorage.getItem(STICKY_KEY);
    return raw ? (JSON.parse(raw) as Persona) : null;
  } catch {
    return null;
  }
}

let current: Persona | null = typeof window === "undefined" ? null : restore();
let bench: Persona[] = [];
const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((cb) => cb());
}

export function subscribePersona(cb: () => void) {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}

export function getActivePersona(): Persona | null {
  return current;
}

export function setActivePersona(p: Persona | null) {
  current = p;
  try {
    if (p) sessionStorage.setItem(STICKY_KEY, JSON.stringify(p));
    else sessionStorage.removeItem(STICKY_KEY);
  } catch {
    /* private mode: sticky-in-memory is still better than nothing */
  }
  emit();
}

export function setBench(list: Persona[]) {
  bench = list;
  emit();
}

export function findPersona(slug: string): Persona | null {
  return bench.find((p) => p.slug === slug) ?? null;
}

/** Transform an outgoing chat message under the sticky persona rules.
 *  Returns the wire text, updating sticky state when the user typed an
 *  explicit /as (which also acts as the mode switch). */
export function outgoing(text: string): string {
  const explicit = /^\/as\s+([a-z0-9-]+)\s+\S/i.exec(text.trim());
  if (explicit) {
    const p = findPersona(explicit[1].toLowerCase());
    if (p) setActivePersona(p); // unknown slugs pass through; backend explains
    return text;
  }
  // "@" as well as "/": a leading @slug invokes one specialist for one message
  // (routes/chat.py rewrites it into the /as form), and prefixed with the
  // sticky persona it never reaches that rewrite — the picker offered
  // "Specialists → growth-mentor" and the message went to whoever was sticky.
  // Deliberately EVERY leading @, not only a bench slug: this runs before the
  // send, where a person and a specialist are indistinguishable. "@mira ..."
  // losing the sticky persona for one turn is the cheaper wrong answer.
  if (/^[/@]/.test(text.trimStart())) return text; // commands stay unprefixed
  if (current) return `/as ${current.slug} ${text}`;
  return text;
}
