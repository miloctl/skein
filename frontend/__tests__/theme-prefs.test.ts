import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  APPEARANCE_KEY,
  CUSTOM_DEFAULT,
  CUSTOM_KEY,
  PACK_KEY,
  THEME_KEY,
  adoptServerTheme,
  applyPrefs,
  applyThemeCode,
  getColorway,
  getCustomHues,
  getPack,
  setColorway,
} from "@/lib/theme";

/** Theme prefs are the one piece of state that must never take the page
 *  down: every reader falls back to a working default on garbage, and a
 *  rejected theme code leaves ZERO residue — a half-applied code is a theme
 *  no picker can reproduce or undo. */

const mocks = vi.hoisted(() => ({
  api: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: mocks.api,
  // "anonymous" keeps pushTheme inert, so tests exercise storage and DOM
  // without a debounced network write firing 800ms later
  getUser: () => "anonymous",
  getApiKey: () => "",
  API_URL: "http://backend.test",
}));

beforeEach(() => {
  mocks.api.mockReset();
});

afterEach(() => {
  const root = document.documentElement;
  delete root.dataset.theme;
  delete root.dataset.appearance;
  delete root.dataset.pack;
  for (const k of ["--thread", "--thread-solid", "--weld", "--weld-solid"]) {
    root.style.removeProperty(k);
  }
});

describe("stored garbage never breaks the theme", () => {
  it("an unknown colorway id reads as the default", () => {
    localStorage.setItem(THEME_KEY, "vantablack");
    expect(getColorway()).toBe("indigo");
  });

  it("an unknown pack id reads as the default", () => {
    localStorage.setItem(PACK_KEY, "brutalist");
    expect(getPack()).toBe("loom");
  });

  it("unparseable custom hues read as the safe default", () => {
    localStorage.setItem(CUSTOM_KEY, "{not json");
    expect(getCustomHues()).toEqual(CUSTOM_DEFAULT);
  });

  it("out-of-range hues are normalized into [0, 360)", () => {
    localStorage.setItem(CUSTOM_KEY, JSON.stringify({ thread: -30, weld: 400 }));
    expect(getCustomHues()).toEqual({ thread: 330, weld: 40 });
  });
});

describe("applyPrefs stamps the root element", () => {
  it("a non-default colorway lands in data-theme; the default removes it", () => {
    localStorage.setItem(THEME_KEY, "madder");
    applyPrefs();
    expect(document.documentElement.dataset.theme).toBe("madder");
    localStorage.removeItem(THEME_KEY);
    applyPrefs();
    expect(document.documentElement.dataset.theme).toBeUndefined();
  });

  it("custom sets the accent tokens inline and a preset clears them again", () => {
    localStorage.setItem(THEME_KEY, "custom");
    localStorage.setItem(CUSTOM_KEY, JSON.stringify({ thread: 200, weld: 65 }));
    applyPrefs();
    const style = document.documentElement.style;
    expect(style.getPropertyValue("--thread")).toContain(" 200)");
    expect(style.getPropertyValue("--weld")).toContain(" 65)");
    // a stale inline token would silently override every preset that follows
    localStorage.setItem(THEME_KEY, "madder");
    applyPrefs();
    expect(style.getPropertyValue("--thread")).toBe("");
    expect(style.getPropertyValue("--weld-solid")).toBe("");
  });
});

describe("a pasted theme code applies whole or not at all", () => {
  it("rejects garbage without writing anything", () => {
    expect(applyThemeCode("{oops")).toBe(false);
    expect(applyThemeCode('{"pack":"nonexistent"}')).toBe(false);
    expect(localStorage.length).toBe(0);
  });

  it("rejects a custom code whose hues are not numbers, with zero residue", () => {
    expect(applyThemeCode('{"colorway":"custom","custom":{"thread":"x"}}')).toBe(
      false,
    );
    expect(localStorage.length).toBe(0);
  });

  it("applies a full valid code to storage and the page", () => {
    const code = '{"pack":"ledger","colorway":"madder","appearance":"dark"}';
    expect(applyThemeCode(code)).toBe(true);
    expect(localStorage.getItem(PACK_KEY)).toBe("ledger");
    expect(localStorage.getItem(THEME_KEY)).toBe("madder");
    expect(localStorage.getItem(APPEARANCE_KEY)).toBe("dark");
    expect(document.documentElement.dataset.pack).toBe("ledger");
    expect(document.documentElement.dataset.appearance).toBe("dark");
  });

  it("applies a custom code through the same validation", () => {
    expect(
      applyThemeCode('{"colorway":"custom","custom":{"thread":10,"weld":20}}'),
    ).toBe(true);
    expect(localStorage.getItem(THEME_KEY)).toBe("custom");
    expect(getCustomHues()).toEqual({ thread: 10, weld: 20 });
  });
});

describe("the theme follows the person, not the browser", () => {
  const profile = (theme: string, team = "") =>
    mocks.api.mockResolvedValue({ theme, team_default: team });

  it("a browser with a local choice never adopts the profile", async () => {
    localStorage.setItem(THEME_KEY, "coral");
    profile('{"colorway":"madder"}');
    expect(await adoptServerTheme()).toBeNull();
    expect(getColorway()).toBe("coral");
  });

  it("an empty browser adopts the profile theme", async () => {
    profile('{"pack":"phosphor","colorway":"verdigris","appearance":"dark"}');
    expect(await adoptServerTheme()).toBe("profile");
    expect(getPack()).toBe("phosphor");
    expect(getColorway()).toBe("verdigris");
    expect(localStorage.getItem(APPEARANCE_KEY)).toBe("dark");
  });

  it("a team default adopts as 'team' and a later profile still supersedes it", async () => {
    profile("", '{"colorway":"bone"}');
    expect(await adoptServerTheme()).toBe("team");
    expect(getColorway()).toBe("bone");
    // the team keys do not count as a personal opinion
    profile('{"colorway":"graphite"}');
    expect(await adoptServerTheme()).toBe("profile");
    expect(getColorway()).toBe("graphite");
  });

  it("a theme picked while the fetch is in flight is never clobbered", async () => {
    let resolve!: (v: unknown) => void;
    mocks.api.mockReturnValue(new Promise((r) => (resolve = r)));
    const adopting = adoptServerTheme();
    // wait until the request is actually in flight: a choice made before
    // the first opinion check exercises that check, not the re-check after
    // the await, and a removed re-check would pass this test
    await vi.waitFor(() => expect(mocks.api).toHaveBeenCalled());
    setColorway("coral"); // the human chooses while the profile is loading
    resolve({ theme: '{"colorway":"madder"}', team_default: "" });
    expect(await adopting).toBeNull();
    expect(getColorway()).toBe("coral");
  });
});
