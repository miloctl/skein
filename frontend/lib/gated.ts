/** Whether the auth gate is currently standing in for the page.
 *
 *  components/auth-gate.tsx renders INSTEAD of the page, but the nav shell
 *  around it and the two overlays mounted beside that shell in app/layout.tsx
 *  are not its children - they keep rendering, and both overlays out-rank
 *  the gate on z-index. Without this store, `?task=12` in a digest link opens
 *  an aria-modal dialog on top of the gate, and the Capture button opens a box
 *  that can only ever answer 401. Each of them reads the store through
 *  useSyncExternalStore and stands down. Each sets inert only on nodes it owns
 *  (components/nav.tsx on its own header and sidebar): an attribute set on a
 *  node another component owns is lost whenever that component re-creates it.
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
