import { afterEach, describe, expect, it, vi } from "vitest";

import { COMMANDS, matchCommands } from "@/lib/commands";
import {
  PACKS,
  getColorway,
  nextColorway,
  setColorway,
  setCustomHues,
} from "@/lib/theme";

/** The ⌘K commands. The filter decides what a keystroke shows, and a command
 *  must land the same state the Settings control does, or the two doors
 *  disagree about what "Theme: Ledger" means. */

vi.mock("@/lib/api", () => ({
  api: vi.fn(),
  getUser: () => "anonymous", // keeps the debounced profile push inert
  getApiKey: () => "",
  API_URL: "http://backend.test",
}));

afterEach(() => {
  window.localStorage.clear();
  const root = document.documentElement;
  delete root.dataset.theme;
  delete root.dataset.appearance;
  delete root.dataset.pack;
});

describe("matchCommands", () => {
  it("matches a mode by its word and nothing else", () => {
    expect(matchCommands("dark").map((c) => c.label)).toEqual(["Mode: dark"]);
  });
  it("matches every pack on the word theme", () => {
    expect(matchCommands("theme")).toHaveLength(PACKS.length);
  });
  it("matches nothing under two characters, or on an ask or a reference", () => {
    expect(matchCommands("")).toEqual([]);
    expect(matchCommands("t")).toEqual([]);
    expect(matchCommands("?theme")).toEqual([]);
    expect(matchCommands("#42")).toEqual([]);
  });
});

describe("a command lands the same state as Settings", () => {
  it("a theme command takes the pack and its signature accent", () => {
    COMMANDS.find((c) => c.id === "pack-ledger")!.run();
    expect(document.documentElement.dataset.pack).toBe("ledger");
    expect(getColorway()).toBe("madder");
  });
  it("a mode command stamps the appearance", () => {
    COMMANDS.find((c) => c.id === "mode-dark")!.run();
    expect(document.documentElement.dataset.appearance).toBe("dark");
  });
});

describe("nextColorway", () => {
  it("wraps from the last named colorway to the first", () => {
    setColorway("bone");
    nextColorway();
    expect(getColorway()).toBe("indigo");
  });
  it("re-enters the named list from custom and keeps the hues stored", () => {
    setCustomHues(10, 20);
    nextColorway();
    expect(getColorway()).toBe("indigo");
    expect(window.localStorage.getItem("skein-custom")).toBe(
      JSON.stringify({ thread: 10, weld: 20 }),
    );
  });
});
