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
  { id: "loom", label: "Loom", subtitle: "Warm, woven, rounded", accent: "indigo" },
  { id: "ledger", label: "Ledger", subtitle: "Broadsheet — ruled, square, serif", accent: "madder" },
  { id: "phosphor", label: "Phosphor", subtitle: "Terminal — mono, scanlines, glow", accent: "verdigris" },
  { id: "atelier", label: "Atelier", subtitle: "Editorial — serif, soft, gallery", accent: "madder" },
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
  // store defaults literally: "chose loom" must be distinguishable from
  // "never chose", or the profile theme hijacks a deliberate reset on load
  write(PACK_KEY, id);
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

const ADOPTED_KEY = "skein-adopted";

function applyAndPing() {
  write(ADOPTED_KEY, null); // an explicit choice is no longer an adoption
  applyPrefs();
  // same-tab subscribers (useSyncExternalStore) listen for this
  window.dispatchEvent(new Event("storage"));
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

function pushTheme() {
  import("./api").then(({ api, getUser }) => {
    if (getUser() === "anonymous") return;
    if (pushTimer) clearTimeout(pushTimer);
    pushTimer = setTimeout(() => {
      pushTimer = null;
      api("/api/users/theme", {
        method: "POST",
        body: JSON.stringify({ theme: serialize() }),
      }).catch(() => {});
    }, 800);
  });
}

// a change made <800ms before tab close must still reach the profile
if (typeof window !== "undefined") {
  window.addEventListener("pagehide", () => {
    if (!pushTimer) return;
    clearTimeout(pushTimer);
    pushTimer = null;
    import("./api").then(({ API_URL, getUser }) => {
      const user = getUser();
      if (user === "anonymous") return;
      fetch(`${API_URL}/api/users/theme`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-User": user },
        body: JSON.stringify({ theme: serialize() }),
        keepalive: true,
      }).catch(() => {});
    });
  });
}

/** Shareable theme code (TP5): the whole theme is ~5 JSON fields — validate
 *  a pasted blob and apply it through the normal setters. */
export function applyThemeCode(code: string): boolean {
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
    if (isCustom && (!Number.isFinite(thread) || !Number.isFinite(weld))) return false;
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
    if (t.appearance === "light" || t.appearance === "dark" || t.appearance === "system") {
      write(APPEARANCE_KEY, t.appearance);
    }
    applyAndPing();
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
    read(THEME_KEY) || read(PACK_KEY) || read(APPEARANCE_KEY) || read(CUSTOM_KEY),
  );
  return hasKeys && read(ADOPTED_KEY) !== "team";
}

export async function adoptServerTheme() {
  const { api } = await import("./api");
  if (browserHasOpinion()) return;
  try {
    // anonymous browsers still adopt the team default (TP3)
    const r = await api<{ theme: string; team_default: string }>("/api/users/theme");
    const blob = r.theme || r.team_default;
    if (!blob) return;
    // re-check after the await: a theme picked while the fetch was in
    // flight must never be clobbered by the profile copy
    if (browserHasOpinion()) return;
    write(ADOPTED_KEY, r.theme ? "profile" : "team");
    const t = JSON.parse(blob);
    if (PACKS.some((p) => p.id === t.pack)) write(PACK_KEY, t.pack === "loom" ? null : t.pack);
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
    applyPrefs();
    window.dispatchEvent(new Event("storage"));
  } catch {}
}

export function setColorway(id: string) {
  write(THEME_KEY, id);
  applyAndPing();
}

export function setCustomHues(thread: number, weld: number) {
  write(CUSTOM_KEY, JSON.stringify({ thread, weld }));
  write(THEME_KEY, "custom");
  applyAndPing();
}

export function setAppearance(id: string) {
  write(APPEARANCE_KEY, id);
  applyAndPing();
}
