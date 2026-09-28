import { useSyncExternalStore } from "react";

import { isGated } from "@/lib/gated";

/** The C key opens quick capture, and a person can turn that off in
 *  Settings → You. The switch is required, not a nicety: WCAG 2.1.4 (level A)
 *  requires a way to turn off or remap a single-character shortcut, because
 *  speech input types words as keystrokes and a dictated "c" opens a dialog.
 *
 *  Remembered per browser, like the audience pickers (lib/audience.ts). The
 *  in-memory copy is what keeps "off" in force for the rest of the visit
 *  when storage throws (private mode, blocked site data). */
const KEY = "skein-capture-key";
const EVENT = "skein-capture-key-change";
let memory: boolean | null = null;

function captureKeyEnabled(): boolean {
  try {
    const stored = localStorage.getItem(KEY);
    if (stored !== null) return stored !== "off";
  } catch {
    /* storage blocked: fall through to this visit's choice */
  }
  return memory ?? true;
}

export function setCaptureKeyEnabled(on: boolean) {
  memory = on;
  try {
    localStorage.setItem(KEY, on ? "on" : "off");
  } catch {
    /* storage blocked: `memory` holds the choice until the page reloads */
  }
  window.dispatchEvent(new Event(EVENT));
}

function subscribe(cb: () => void) {
  window.addEventListener(EVENT, cb);
  window.addEventListener("storage", cb);
  return () => {
    window.removeEventListener(EVENT, cb);
    window.removeEventListener("storage", cb);
  };
}

export function useCaptureKeyEnabled(): boolean {
  return useSyncExternalStore(subscribe, captureKeyEnabled, () => true);
}

/** Whether this keydown opens quick capture. Only a bare C counts, Caps Lock
 *  included: Ctrl+C and ⌘C copy, and Alt+C types a character on some
 *  layouts. Never
 *  while a text field has focus, where C is a letter someone is typing, and
 *  never while a modal owns focus (the capture palette, the task panel): the
 *  same test the ⌘K search shortcut makes (components/nav-search.tsx). */
export function opensCapture(e: KeyboardEvent): boolean {
  if (e.key !== "c" && e.key !== "C") return false;
  if (e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return false;
  if (e.repeat || e.isComposing || e.defaultPrevented) return false;
  const target = e.target instanceof HTMLElement ? e.target : null;
  if (target?.closest("input, textarea, select, [contenteditable]:not([contenteditable='false'])"))
    return false;
  if (isGated() || document.querySelector('[aria-modal="true"]:not(#chat-list):not(#navigation-drawer)'))
    return false;
  return captureKeyEnabled();
}
