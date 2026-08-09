/** Whether the auth gate is currently standing in for the page.
 *
 *  components/auth-gate.tsx renders INSTEAD of the page, but the nav and the
 *  two overlays mounted beside it in app/layout.tsx are its siblings, not its
 *  children — they keep rendering, and both overlays out-rank the gate on
 *  z-index. `?task=12` in a digest link opened an aria-modal dialog on top of
 *  the gate, and ⌘K opened a capture box that could only ever answer 401. A
 *  DOM-level fix (setAttribute on the header) reached into a node another
 *  component owns and was lost whenever that component re-created it, so the
 *  state is published here and every sibling reads it as a prop.
 */

let gated = false;
const subscribers = new Set<() => void>();

export function setGated(value: boolean): void {
  if (value === gated) return;
  gated = value;
  for (const cb of subscribers) cb();
}

export function isGated(): boolean {
  return gated;
}

export function subscribeGated(cb: () => void): () => void {
  subscribers.add(cb);
  return () => {
    subscribers.delete(cb);
  };
}
