// Theme prefs live in this browser, like identity. Two axes:
// appearance (system/light/dark -> color-scheme) and colorway (accent dyes).
// globals.css owns the preset values; layout.tsx applies these before paint
// with an inline script that mirrors this logic (keep the two in sync).

const THEME_KEY = "skein-theme";
const APPEARANCE_KEY = "skein-appearance";
const CUSTOM_KEY = "skein-custom";
const PACK_KEY = "skein-pack";

// Fabric packs re-weave surfaces, texture, and type (globals.css owns the
// values); colorways and custom hues dye the accents on top of any pack.
// In Settings a pack is a "theme card": picking one also applies its
// signature accent, and the accent stays overridable under Customize.
export const PACKS = [
  { id: "loom", label: "Loom", subtitle: "Warm, woven", accent: "indigo" },
  { id: "ledger", label: "Ledger", subtitle: "Cool paper, ruled", accent: "madder" },
  { id: "phosphor", label: "Phosphor", subtitle: "Terminal green", accent: "verdigris" },
  { id: "contrast", label: "High contrast", subtitle: "Maximum legibility", accent: "graphite" },
] as const;

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

// Custom colorway: the user dyes the two accent threads by hue; lightness
// and chroma are fixed at values sweep-verified to pass WCAG AA against
// every surface at EVERY hue (scratchpad hue_sweep.py, worst case 5.27:1),
// so no dial position can make the UI unreadable.
export const CUSTOM_DEFAULT = { thread: 264, weld: 65 };

function customCss(threadHue: number, weldHue: number) {
  const t = ((Math.round(threadHue) % 360) + 360) % 360;
  const w = ((Math.round(weldHue) % 360) + 360) % 360;
  return {
    "--thread": `light-dark(oklch(0.44 0.13 ${t}), oklch(0.8 0.09 ${t}))`,
    "--thread-solid": `light-dark(oklch(0.44 0.13 ${t}), oklch(0.5 0.13 ${t}))`,
    "--weld": `light-dark(oklch(0.47 0.09 ${w}), oklch(0.78 0.09 ${w}))`,
  };
}

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
  if (t === "custom") return "custom";
  return COLORWAYS.some((c) => c.id === t) ? (t as string) : "indigo";
}

export function getCustomHues(): { thread: number; weld: number } {
  if (typeof window === "undefined") return CUSTOM_DEFAULT;
  try {
    const raw = JSON.parse(read(CUSTOM_KEY) || "");
    const thread = Number(raw.thread);
    const weld = Number(raw.weld);
    if (Number.isFinite(thread) && Number.isFinite(weld)) {
      return {
        thread: ((Math.round(thread) % 360) + 360) % 360,
        weld: ((Math.round(weld) % 360) + 360) % 360,
      };
    }
  } catch {}
  return CUSTOM_DEFAULT;
}

export function getAppearance(): string {
  if (typeof window === "undefined") return "system";
  const a = read(APPEARANCE_KEY);
  return a === "light" || a === "dark" ? a : "system";
}

export function getPack(): string {
  if (typeof window === "undefined") return "loom";
  const p = read(PACK_KEY);
  return PACKS.some((x) => x.id === p) ? (p as string) : "loom";
}

export function setPack(id: string) {
  write(PACK_KEY, id === "loom" ? null : id);
  applyAndPing();
}

export function applyPrefs() {
  const root = document.documentElement;
  const t = getColorway();
  if (t === "indigo") delete root.dataset.theme;
  else root.dataset.theme = t;
  if (t === "custom") {
    const { thread, weld } = getCustomHues();
    for (const [k, v] of Object.entries(customCss(thread, weld))) {
      root.style.setProperty(k, v);
    }
  } else {
    for (const k of ["--thread", "--thread-solid", "--weld"]) {
      root.style.removeProperty(k);
    }
  }
  const a = getAppearance();
  if (a === "system") delete root.dataset.appearance;
  else root.dataset.appearance = a;
  const p = getPack();
  if (p === "loom") delete root.dataset.pack;
  else root.dataset.pack = p;
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

export function setCustomHues(thread: number, weld: number) {
  write(CUSTOM_KEY, JSON.stringify({ thread, weld }));
  write(THEME_KEY, "custom");
  applyAndPing();
}

export function setAppearance(id: string) {
  write(APPEARANCE_KEY, id === "system" ? null : id);
  applyAndPing();
}
