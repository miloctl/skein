/** Whether the auth gate is currently standing in for the page.
 *
 *  components/auth-gate.tsx renders INSTEAD of the page, but the nav shell
 *  around it and the two overlays mounted beside that shell in app/layout.tsx
 *  are not its children - they keep rendering, and both overlays out-rank
 *  the gate on z-index. Without this store, `?task=12` in a digest link opens
 *  an aria-modal dialog on top of the gate, and the Capture button opens a box
 *  that can only ever answer 401. Each of them reads the store through
 *  useSyncExternalStore and stands down. An open overlay sets inert on every
 *  body sibling except its own and the status regions
 *  (components/task-peek.tsx, components/capture-palette.tsx), and removes it
 *  on cleanup. components/nav.tsx sets it on its own header and sidebar while
 *  gated. All three set it from an effect, never a rendered prop: React does
 *  not re-apply an attribute it believes is already set, so one removal by
 *  another owner's cleanup would be permanent.
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

/** Whether the gate or a modal owns focus, so a global shortcut (the C
 *  capture key in lib/capture-key.ts, the ⌘K search in
 *  components/nav-search.tsx) stands down. Capture and TaskPeek own their
 *  drafts and focus: a shortcut over either opens a second dialog or moves
 *  focus to the inert shell behind it. The chat drawer (#chat-list) and the
 *  navigation drawer hold no draft and are excluded. */
export function modalOwnsFocus(): boolean {
  return (
    gated ||
    document.querySelector(
      '[aria-modal="true"]:not(#chat-list):not(#navigation-drawer)',
    ) !== null
  );
}
