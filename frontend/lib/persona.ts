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

let current: Persona | null = null;
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
  if (text.trimStart().startsWith("/")) return text; // commands stay unprefixed
  if (current) return `/as ${current.slug} ${text}`;
  return text;
}
