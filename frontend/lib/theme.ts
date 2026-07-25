// Theme prefs live in this browser, like identity. Two axes:
// appearance (system/light/dark -> color-scheme) and colorway (accent dyes).
// globals.css owns the actual values; layout.tsx applies these before paint
// with an inline script that mirrors this logic (keep the two in sync).

const THEME_KEY = "skein-theme";
const APPEARANCE_KEY = "skein-appearance";

export const COLORWAYS = [
  { id: "indigo", label: "Indigo & ochre", thread: "#3b4dbf", weld: "#935a1c" },
  { id: "madder", label: "Madder & woad", thread: "#a92c40", weld: "#48628f" },
  { id: "verdigris", label: "Verdigris & copper", thread: "#1c6e66", weld: "#9d4f28" },
  { id: "graphite", label: "Graphite & brass", thread: "#45413a", weld: "#8a5e14" },
] as const;

export const APPEARANCES = [
  { id: "system", label: "System" },
  { id: "light", label: "Light" },
  { id: "dark", label: "Dark" },
] as const;

// storage can throw (blocked third-party contexts, some private modes) —
// theme prefs must never take the page down with them
function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string | null) {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch {}
}

export function getColorway(): string {
  if (typeof window === "undefined") return "indigo";
  const t = read(THEME_KEY);
  return COLORWAYS.some((c) => c.id === t) ? (t as string) : "indigo";
}

export function getAppearance(): string {
  if (typeof window === "undefined") return "system";
  const a = read(APPEARANCE_KEY);
  return a === "light" || a === "dark" ? a : "system";
}

export function applyPrefs() {
  const root = document.documentElement;
  const t = getColorway();
  if (t === "indigo") delete root.dataset.theme;
  else root.dataset.theme = t;
  const a = getAppearance();
  if (a === "system") delete root.dataset.appearance;
  else root.dataset.appearance = a;
}

function applyAndPing() {
  applyPrefs();
  // same-tab subscribers (useSyncExternalStore) listen for this
  window.dispatchEvent(new Event("storage"));
}

export function setColorway(id: string) {
  write(THEME_KEY, id === "indigo" ? null : id);
  applyAndPing();
}

export function setAppearance(id: string) {
  write(APPEARANCE_KEY, id === "system" ? null : id);
  applyAndPing();
}
