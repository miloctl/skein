import { describe, expect, it, vi } from "vitest";

import {
  SIDEBAR_KEY,
  getSidebarCollapsed,
  serverSidebarCollapsed,
  subscribeChatLayout,
  toggleSidebar,
} from "@/lib/chat-layout";

/** The stored shape is the contract: collapsed writes "1", expanded REMOVES
 *  the key (never writes "0"). Preferences saved by the old in-sidebar
 *  toggle read as collapsed only because of that remove-on-expand rule, and
 *  layout.tsx's pre-paint script (lib/theme-boot.ts) stamps <html> from the
 *  same key, so a changed value silently breaks first paint. */

describe("chat sidebar preference", () => {
  it("defaults to expanded in a browser with no stored preference", () => {
    expect(getSidebarCollapsed()).toBe(false);
  });

  it("collapse stores '1' under the shared key and stamps <html>", () => {
    toggleSidebar();
    expect(localStorage.getItem(SIDEBAR_KEY)).toBe("1");
    expect(getSidebarCollapsed()).toBe(true);
    expect(document.documentElement.dataset.chatSidebar).toBe("collapsed");
  });

  it("expand removes the key instead of writing a falsy value", () => {
    toggleSidebar(); // collapse
    toggleSidebar(); // expand
    expect(localStorage.getItem(SIDEBAR_KEY)).toBeNull();
    expect(getSidebarCollapsed()).toBe(false);
    expect(document.documentElement.dataset.chatSidebar).toBe("");
  });

  it("notifies subscribers on toggle and stops after unsubscribe", () => {
    const cb = vi.fn();
    const unsubscribe = subscribeChatLayout(cb);
    toggleSidebar();
    expect(cb).toHaveBeenCalledTimes(1);
    unsubscribe();
    toggleSidebar();
    expect(cb).toHaveBeenCalledTimes(1);
  });

  it("server render always reports expanded", () => {
    // the server cannot read localStorage; the pre-paint script covers the
    // gap, so this must stay a constant and never guess
    expect(serverSidebarCollapsed()).toBe(false);
  });
});
