// Theme prefs live in this browser, like identity. Two axes:
// appearance (system/light/dark -> color-scheme) and colorway (accent dyes).
// globals.css owns the preset values.
//
// This module is the SINGLE SOURCE for the pack and colorway ids, the storage
// keys, and the custom-hue formula. It used to be one of three copies:
// layout.tsx repeated the ids and formulas inside its pre-paint script, and
// scripts/check_theme_contrast.py repeated the formulas again, with only a
// comment holding them together. That drift shipped the theme-revert bug
// twice. Now layout.tsx GENERATES its script from the values below
// (lib/theme-boot.ts) and the contrast checker PARSES them out of this file,
// so there is nothing left to keep in sync by hand.

import { sessionRevision } from "./auth";

export const THEME_KEY = "skein-theme";
export const APPEARANCE_KEY = "skein-appearance";
export const CUSTOM_KEY = "skein-custom";
export const PACK_KEY = "skein-pack";

// The ids that need no data attribute: globals.css carries them on :root.
export const DEFAULT_PACK = "loom";
export const DEFAULT_COLORWAY = "indigo";

// Fabric packs re-weave surfaces, texture, and type (globals.css owns the
// values); colorways and custom hues dye the accents on top of any pack.
// In Settings a pack is a "theme card": picking one also applies its
// signature accent, and the accent stays overridable under Customize.
export const PACKS = [
  {
    id: "loom",
    label: "Loom",
    subtitle: "Warm, woven, rounded",
    accent: "indigo",
  },
  {
    id: "ledger",
    label: "Ledger",
    subtitle: "Broadsheet — ruled, square, serif",
    accent: "madder",
  },
  {
    id: "phosphor",
    label: "Phosphor",
    subtitle: "Terminal — mono, scanlines, glow",
    accent: "verdigris",
  },
  {
    id: "atelier",
    label: "Atelier",
    subtitle: "Editorial — serif, soft, gallery",
    accent: "madder",
  },
  {
    id: "claw",
    label: "Claw",
    subtitle: "Command deck — charcoal, coral",
    accent: "coral",
  },
  {
    id: "hermes",
    label: "Hermes",
    subtitle: "Mission console — cream on teal",
    accent: "bone",
  },
  {
    id: "contrast",
    label: "High contrast",
    subtitle: "Maximum legibility",
    accent: "graphite",
  },
] as const;

export const COLORWAYS = [
  { id: "indigo", label: "Indigo & ochre", thread: "#3b4dbf", weld: "#935a1c" },
  { id: "madder", label: "Madder & woad", thread: "#a92c40", weld: "#48628f" },
  {
    id: "verdigris",
    label: "Verdigris & copper",
    thread: "#1b6a63",
    weld: "#9d4f28",
  },
  {
    id: "graphite",
    label: "Graphite & brass",
    thread: "#45413a",
    weld: "#8a5e14",
  },
  { id: "coral", label: "Coral & teal", thread: "#a63b29", weld: "#0f766e" },
  { id: "bone", label: "Bone & jade", thread: "#74581f", weld: "#047857" },
] as const;

export const APPEARANCES = [
  { id: "system", label: "System" },
  { id: "light", label: "Light" },
  { id: "dark", label: "Dark" },
] as const;

// Custom colorway: the user dyes the two accent threads by hue; lightness
// and chroma are fixed at values that pass WCAG AA against every pack
// surface at EVERY hue, so no dial position can make the UI unreadable.
export const CUSTOM_DEFAULT = { thread: 264, weld: 65 };

// The fixed half of the custom colorway: per CSS token, which hue dial feeds
// it, what it must stay legible against, and the (lightness, chroma) it wears
// in each mode. Only the hue varies.
//
//   on: "surface"  the token is INK — swept against every pack surface
//   on: "white"    the token is a solid FILL under white text (bg-thread-solid)
//
// scripts/check_theme_contrast.py PARSES this table (it does not copy it) and
// sweeps all 360 integer hues of every row on each lint run, printing the
// floor. So a change here is checked on the next lint, and a row added here is
// swept without touching the checker. Keep the literal shape regular: one
// token per line, `[lightness, chroma]`.
export const CUSTOM_LC = {
  "--thread": {
    hue: "thread",
    on: "surface",
    light: [0.44, 0.13],
    dark: [0.8, 0.09],
  },
  "--thread-solid": {
    hue: "thread",
    on: "white",
    light: [0.44, 0.13],
    dark: [0.5, 0.13],
  },
  "--weld": {
    hue: "weld",
    on: "surface",
    light: [0.47, 0.09],
    dark: [0.78, 0.09],
  },
  // the fill half, same hue: white text sits on it in BOTH modes, so both
  // halves are the dark lightness. Without this a custom hue had no fill
  // token and bg-weld (the ink) carried white text at ~2:1 in dark.
  "--weld-solid": {
    hue: "weld",
    on: "white",
    light: [0.45, 0.09],
    dark: [0.45, 0.09],
  },
} as const;

function normalizeHue(n: number): number {
  return ((Math.round(n) % 360) + 360) % 360;
}

function customCss(threadHue: number, weldHue: number): Record<string, string> {
  const hues = { thread: normalizeHue(threadHue), weld: normalizeHue(weldHue) };
  const css: Record<string, string> = {};
  for (const [token, lc] of Object.entries(CUSTOM_LC)) {
    const h = hues[lc.hue];
    css[token] =
      `light-dark(oklch(${lc.light[0]} ${lc.light[1]} ${h}), oklch(${lc.dark[0]} ${lc.dark[1]} ${h}))`;
  }
  return css;
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
  if (typeof window === "undefined") return DEFAULT_COLORWAY;
  const t = read(THEME_KEY);
  if (t === "custom") return "custom";
  return COLORWAYS.some((c) => c.id === t) ? (t as string) : DEFAULT_COLORWAY;
}

export function getCustomHues(): { thread: number; weld: number } {
  if (typeof window === "undefined") return CUSTOM_DEFAULT;
  try {
    const raw = JSON.parse(read(CUSTOM_KEY) || "");
    const thread = Number(raw.thread);
    const weld = Number(raw.weld);
    if (Number.isFinite(thread) && Number.isFinite(weld)) {
      return { thread: normalizeHue(thread), weld: normalizeHue(weld) };
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
  if (typeof window === "undefined") return DEFAULT_PACK;
  const p = read(PACK_KEY);
  return PACKS.some((x) => x.id === p) ? (p as string) : DEFAULT_PACK;
}

/** `fade: false` skips the crossfade (the Settings page, where a fade on the
 *  control just clicked reads as lag). `accent: true` also takes the pack's
 *  signature colorway, in the SAME paint — two setters back to back start two
 *  view transitions, and the second skips the first mid-fade. */
export type ApplyOpts = { fade?: boolean; accent?: boolean };

export function setPack(id: string, opts: ApplyOpts = {}) {
  // store defaults literally: "chose loom" must be distinguishable from
  // "never chose", or the profile theme hijacks a deliberate reset on load
  write(PACK_KEY, id);
  if (opts.accent) {
    const pack = PACKS.find((p) => p.id === id);
    if (pack) write(THEME_KEY, pack.accent);
  }
  applyAndPing(opts);
}

export function applyPrefs() {
  const root = document.documentElement;
  const t = getColorway();
  if (t === DEFAULT_COLORWAY) delete root.dataset.theme;
  else root.dataset.theme = t;
  if (t === "custom") {
    const { thread, weld } = getCustomHues();
    for (const [k, v] of Object.entries(customCss(thread, weld))) {
      root.style.setProperty(k, v);
    }
  } else {
    for (const k of Object.keys(CUSTOM_LC)) {
      root.style.removeProperty(k);
    }
  }
  const a = getAppearance();
  if (a === "system") delete root.dataset.appearance;
  else root.dataset.appearance = a;
  const p = getPack();
  if (p === DEFAULT_PACK) delete root.dataset.pack;
  else root.dataset.pack = p;
  syncThemeColor();
}

// The mobile address bar reads <meta name="theme-color">, but the page colour
// is a function of the pack as well as the appearance — #faf9f6 under loom,
// #f2f5ef under phosphor, #ffffff under contrast. The static pair in
// layout.tsx's viewport export would be visibly wrong for most packs on the
// one device where the bar is actually visible, so read the resolved colour
// back out of the DOM after applying prefs.
// Exported because applyPrefs does NOT run on a normal page load — the
// pre-paint script in layout.tsx sets the data attributes and applyPrefs only
// fires on a setter, a cross-tab storage event, or profile adoption.
// ThemeSync calls this on mount to cover the ordinary case.
export function syncThemeColor() {
  const resolved = getComputedStyle(document.body).backgroundColor;
  if (!resolved) return;
  let meta = document.querySelector<HTMLMetaElement>(
    'meta[name="theme-color"]:not([media])',
  );
  if (!meta) {
    meta = document.createElement("meta");
    meta.name = "theme-color";
    document.head.appendChild(meta);
  }
  meta.content = resolved;
}

const ADOPTED_KEY = "skein-adopted";

// A theme change crossfades through the View Transitions API where the
// browser has it (a missing API takes the direct path, and so does jsdom, so
// a test that wants the fade stubs `document.startViewTransition`). The
// browser snapshots the OLD page at the next rendering opportunity and only
// then runs the callback, so every DOM write of the change must happen
// inside it: the same-tab "storage" event is dispatched there too, because
// ThemeSync answers it with applyPrefs, and dispatched synchronously after
// startViewTransition it restyled the page before the snapshot — old and
// new frames identical, no visible fade, and the input block for nothing.
// Reduced motion is honored in globals.css on the ::view-transition
// pseudo-elements, not here.
function paint(fade: boolean) {
  const update = () => {
    applyPrefs();
    // same-tab subscribers (useSyncExternalStore, ThemeSync) listen for this
    window.dispatchEvent(new Event("storage"));
  };
  if (fade && typeof document.startViewTransition === "function") {
    document.startViewTransition(update);
  } else {
    update();
  }
}

function applyAndPing({ fade = true }: ApplyOpts = {}) {
  write(ADOPTED_KEY, null); // an explicit choice is no longer an adoption
  paint(fade);
  pushTheme();
}

// --- profile sync: the theme follows the person, not the browser ---------
// Every change auto-saves to the profile (debounced); a browser with no
// local prefs adopts the profile on load. Local prefs win locally — they
// were set deliberately in that browser and immediately re-save anyway.

let pushTimer: ReturnType<typeof setTimeout> | null = null;

function serialize(): string {
  return JSON.stringify({
    pack: getPack(),
    colorway: getColorway(),
    appearance: getAppearance(),
    custom: getCustomHues(),
  });
}

let saveOnPagehide: (() => void) | null = null;
function pushTheme() {
  const owner = sessionRevision();
  import("./api").then(({ api, getUser, API_URL, sessionHeaders }) => {
    if (owner !== sessionRevision() || getUser() === "anonymous") return;
    if (pushTimer) clearTimeout(pushTimer);
    const headers = sessionHeaders();
    // Capture identity before teardown. Reading a newer tab's cookie with old
    // preferences must fail the server's CSRF binding, not update its profile.
    saveOnPagehide = () => {
      if (owner !== sessionRevision()) return;
      void fetch(`${API_URL}/api/users/theme`, {
        method: "POST", credentials: "include", keepalive: true,
        headers: { "Content-Type": "application/json", ...headers },
        body: JSON.stringify({ theme: serialize() }),
      }).catch(() => {});
    };
    pushTimer = setTimeout(() => {
      pushTimer = null;
      saveOnPagehide = null;
      if (owner !== sessionRevision()) return;
      api("/api/users/theme", {
        method: "POST",
        body: JSON.stringify({ theme: serialize() }),
      }).catch(() => {});
    }, 800);
  });
}

if (typeof window !== "undefined") {
  window.addEventListener("pagehide", () => {
    if (!pushTimer) return;
    clearTimeout(pushTimer);
    pushTimer = null;
    saveOnPagehide?.();
    saveOnPagehide = null;
  });
}

/** Shareable theme code (TP5): the whole theme is ~5 JSON fields — validate
 *  a pasted blob and apply it through the normal setters. */
export function applyThemeCode(code: string, opts: ApplyOpts = {}): boolean {
  try {
    const t = JSON.parse(code);
    if (typeof t !== "object" || t === null) return false;
    // validate EVERYTHING before the first write — a rejected code must
    // leave zero residue, and every present field must be reproducible
    const packOk = PACKS.some((p) => p.id === t.pack);
    const colorOk = COLORWAYS.some((c) => c.id === t.colorway);
    const isCustom = t.colorway === "custom";
    const thread = Number(t.custom?.thread);
    const weld = Number(t.custom?.weld);
    if (isCustom && (!Number.isFinite(thread) || !Number.isFinite(weld)))
      return false;
    if (t.pack !== undefined && !packOk) return false;
    if (t.colorway !== undefined && !colorOk && !isCustom) return false;
    if (!packOk && !colorOk && !isCustom) return false;
    if (packOk) write(PACK_KEY, t.pack);
    if (isCustom) {
      write(CUSTOM_KEY, JSON.stringify({ thread, weld }));
      write(THEME_KEY, "custom");
    } else if (colorOk) {
      write(THEME_KEY, t.colorway);
    }
    if (
      t.appearance === "light" ||
      t.appearance === "dark" ||
      t.appearance === "system"
    ) {
      write(APPEARANCE_KEY, t.appearance);
    }
    applyAndPing(opts);
    return true;
  } catch {
    return false;
  }
}

export function themeCode(): string {
  return serialize();
}

// "no opinion yet" = no local keys, OR the keys came from adopting the TEAM
// default (not a human choice) — a personal profile may still supersede that
function browserHasOpinion(): boolean {
  const hasKeys = Boolean(
    read(THEME_KEY) ||
    read(PACK_KEY) ||
    read(APPEARANCE_KEY) ||
    read(CUSTOM_KEY),
  );
  return hasKeys && read(ADOPTED_KEY) !== "team";
}

export async function adoptServerTheme(): Promise<"profile" | "team" | null> {
  const { api } = await import("./api");
  if (browserHasOpinion()) return null;
  try {
    // anonymous browsers still adopt the team default (TP3)
    const r = await api<{ theme: string; team_default: string }>(
      "/api/users/theme",
    );
    const blob = r.theme || r.team_default;
    if (!blob) return null;
    // re-check after the await: a theme picked while the fetch was in
    // flight must never be clobbered by the profile copy
    if (browserHasOpinion()) return null;
    write(ADOPTED_KEY, r.theme ? "profile" : "team");
    const t = JSON.parse(blob);
    if (PACKS.some((p) => p.id === t.pack))
      write(PACK_KEY, t.pack === DEFAULT_PACK ? null : t.pack);
    if (t.colorway === "custom" && t.custom) {
      const thread = Number(t.custom.thread);
      const weld = Number(t.custom.weld);
      if (Number.isFinite(thread) && Number.isFinite(weld)) {
        write(CUSTOM_KEY, JSON.stringify({ thread, weld }));
        write(THEME_KEY, "custom");
      }
    } else if (COLORWAYS.some((c) => c.id === t.colorway)) {
      write(THEME_KEY, t.colorway === "indigo" ? null : t.colorway);
    }
    if (t.appearance === "light" || t.appearance === "dark") {
      write(APPEARANCE_KEY, t.appearance);
    } else if (t.appearance === "system") {
      write(APPEARANCE_KEY, null); // fully supersede an adopted light/dark
    }
    // fades too: the restyle then reads as "your theme arrived" rather than
    // a flash, beside the status pill ThemeSync shows for the same moment
    paint(true);
    return r.theme ? "profile" : "team";
  } catch {
    return null;
  }
}

export function setColorway(id: string, opts: ApplyOpts = {}) {
  write(THEME_KEY, id);
  applyAndPing(opts);
}

/** The next named colorway in list order, wrapping. Custom re-enters at the
 *  first: there is no "next" hue pair, and the stored hues stay put so Custom
 *  in Settings restores them. */
export function nextColorway(opts: ApplyOpts = {}) {
  const i = COLORWAYS.findIndex((c) => c.id === getColorway());
  setColorway(COLORWAYS[(i + 1) % COLORWAYS.length].id, opts);
}

export function setCustomHues(thread: number, weld: number) {
  write(CUSTOM_KEY, JSON.stringify({ thread, weld }));
  write(THEME_KEY, "custom");
  // never fades: a slider drag fires this per tick, and a whole-page
  // snapshot per tick stutters while each new transition skips the last
  applyAndPing({ fade: false });
}

export function setAppearance(id: string, opts: ApplyOpts = {}) {
  write(APPEARANCE_KEY, id);
  applyAndPing(opts);
}
