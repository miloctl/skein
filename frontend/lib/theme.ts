// Theme prefs live in this browser, like identity. Two axes:
// appearance (system/light/dark -> color-scheme) and colorway (accent dyes).
// globals.css owns the actual values; layout.tsx applies these before paint.

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

export function getColorway(): string {
  if (typeof window === "undefined") return "indigo";
  const t = window.localStorage.getItem(THEME_KEY);
  return COLORWAYS.some((c) => c.id === t) ? (t as string) : "indigo";
}

export function getAppearance(): string {
  if (typeof window === "undefined") return "system";
  const a = window.localStorage.getItem(APPEARANCE_KEY);
  return a === "light" || a === "dark" ? a : "system";
}

function apply() {
  const root = document.documentElement;
  const t = getColorway();
  if (t === "indigo") delete root.dataset.theme;
  else root.dataset.theme = t;
  const a = getAppearance();
  if (a === "system") delete root.dataset.appearance;
  else root.dataset.appearance = a;
  // same-tab subscribers (useSyncExternalStore) listen for this
  window.dispatchEvent(new Event("storage"));
}

export function setColorway(id: string) {
  if (id === "indigo") window.localStorage.removeItem(THEME_KEY);
  else window.localStorage.setItem(THEME_KEY, id);
  apply();
}

export function setAppearance(id: string) {
  if (id === "system") window.localStorage.removeItem(APPEARANCE_KEY);
  else window.localStorage.setItem(APPEARANCE_KEY, id);
  apply();
}
