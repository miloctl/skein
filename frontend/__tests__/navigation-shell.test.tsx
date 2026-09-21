import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({ pathname: "/artifacts", desktop: true }));
vi.mock("next/navigation", () => ({ usePathname: () => state.pathname }));
vi.mock("next/link", () => ({
  default: (props: React.ComponentProps<"a">) => <a data-next-link {...props} />,
}));
vi.mock("@/lib/api", async (original) => ({
  ...await original<typeof import("@/lib/api")>(),
  getUser: () => "tester",
  api: async () => ({ inbox: 0, yours: 0, chats: 0 }),
}));
vi.mock("@/lib/auth", async (original) => ({
  ...await original<typeof import("@/lib/auth")>(),
  authConfig: async () => ({ mode: "trusted-header" }),
  sessionLocked: () => false,
}));
vi.mock("@/lib/extensions/context", () => ({
  useFrontendExtensions: () => ({ navigation: [{
    id: "atlas.workplace.manager-nav", label: "Atlas delivery workspace",
    href: "/artifacts?report=atlas#delivery", activePaths: ["/artifacts"],
  }] }),
}));

import { Nav } from "@/components/nav";
import { setGated } from "@/lib/gated";

const mediaListeners = new Set<() => void>();
beforeEach(() => {
  state.pathname = "/artifacts";
  state.desktop = true;
  mediaListeners.clear();
  vi.stubGlobal("matchMedia", () => ({
    matches: state.desktop,
    addEventListener: (_: string, fn: () => void) => mediaListeners.add(fn),
    removeEventListener: (_: string, fn: () => void) => mediaListeners.delete(fn),
  }));
  HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  HTMLDialogElement.prototype.close = function () {
    if (!this.open) return;
    this.open = false;
    this.dispatchEvent(new Event("close"));
  };
});
afterEach(() => {
  act(() => setGated(false));
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("the navigation shell", () => {
  it("exposes active group children and keeps core query state without blocking full extension links", () => {
    render(<Nav />);
    const nav = screen.getByRole("navigation", { name: "Primary" });
    const report = within(nav).getByRole("link", { name: "Reports" });
    expect(report.getAttribute("aria-current")).toBe("page");
    expect(fireEvent.click(report)).toBe(false);
    expect(within(nav).queryByRole("link", { name: "Approvals" })).toBeNull();
    const extension = within(nav).getByRole("link", { name: "Atlas delivery workspace" });
    expect(extension.getAttribute("href")).toBe("/artifacts?report=atlas#delivery");
    expect(fireEvent.click(extension)).toBe(true);
  });

  it("jumps to a same-document fragment only when the path and query already match", () => {
    // Next appends a link's fragment to its cached canonical URL, so a
    // same-document target is a native fragment jump. Any other href stays a
    // soft navigation: a contributed link whose query differs from the address
    // bar never reloads the document.
    window.history.replaceState(null, "", "/artifacts");
    const view = render(<Nav />);
    const extension = screen.getByRole("link", { name: "Atlas delivery workspace" });
    expect(extension.getAttribute("href")).toBe("/artifacts?report=atlas#delivery");
    expect(fireEvent.click(extension)).toBe(true);
    expect(window.location.hash).toBe("");
    window.history.replaceState(null, "", "/artifacts?report=atlas");
    expect(fireEvent.click(screen.getByRole("link", { name: "Atlas delivery workspace" }))).toBe(false);
    expect(window.location.hash).toBe("#delivery");
    state.pathname = "/dashboard";
    window.history.replaceState(null, "", "/dashboard");
    view.rerender(<Nav />);
    const crossRoute = screen.getByRole("link", { name: "Atlas delivery workspace" });
    expect(crossRoute.hasAttribute("data-next-link")).toBe(true);
    expect(fireEvent.click(crossRoute)).toBe(true);
    expect(window.location.hash).toBe("");
  });

  it("recognizes engagement detail as Work", () => {
    state.pathname = "/engagement/4";
    render(<Nav />);
    expect(screen.getByRole("link", { name: "Work" }).getAttribute("aria-current")).toBe("location");
    expect(screen.getByRole("link", { name: "Browse" })).toBeTruthy();
  });

  it("collapses children, preserves destination names, and restores its preference", () => {
    const view = render(<Nav />);
    fireEvent.click(screen.getByRole("button", { name: "Collapse navigation" }));
    expect(screen.queryByRole("link", { name: "Reports" })).toBeNull();
    expect(screen.getByRole("link", { name: "Work" })).toBeTruthy();
    view.unmount();
    render(<Nav />);
    expect(screen.getByRole("button", { name: "Expand navigation" })).toBeTruthy();
  });

  it("still collapses when browser storage is blocked", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    render(<Nav />);
    fireEvent.click(screen.getByRole("button", { name: "Collapse navigation" }));
    expect(screen.getByRole("button", { name: "Expand navigation" })).toBeTruthy();
  });

  it("closes the drawer on a route or breakpoint change and restores scrolling", () => {
    state.desktop = false;
    const view = render(<Nav />);
    fireEvent.click(screen.getByRole("button", { name: "Open navigation" }));
    expect(screen.getByRole("dialog", { name: "Navigation" })).toBeTruthy();
    state.pathname = "/chat";
    view.rerender(<Nav />);
    expect(screen.queryByRole("dialog", { name: "Navigation" })).toBeNull();
    expect(document.body.style.overflow).toBe("");
    fireEvent.click(screen.getByRole("button", { name: "Open navigation" }));
    act(() => {
      state.desktop = true;
      mediaListeners.forEach((changed) => changed());
    });
    expect(screen.queryByRole("dialog", { name: "Navigation" })).toBeNull();
    expect(document.body.style.overflow).toBe("");
    act(() => {
      state.desktop = false;
      mediaListeners.forEach((changed) => changed());
    });
    expect(screen.queryByRole("dialog", { name: "Navigation" })).toBeNull();
  });

  it.each([
    ["/settings", /Settings & access/],
    ["/guide", /Field guide/],
  ])("closes the drawer for an identity-menu link on %s", async (pathname, name) => {
    state.desktop = false;
    state.pathname = pathname;
    render(<Nav />);
    fireEvent.click(screen.getByRole("button", { name: "Open navigation" }));
    fireEvent.click(screen.getByTitle("You — tester"));
    fireEvent.click(await screen.findByRole("menuitem", { name }));
    expect(screen.queryByRole("dialog", { name: "Navigation" })).toBeNull();
    expect(document.body.style.overflow).toBe("");
  });

  it("uses Escape in the identity menu without cancelling the surrounding drawer", () => {
    state.desktop = false;
    render(<Nav />);
    fireEvent.click(screen.getByRole("button", { name: "Open navigation" }));
    const identity = screen.getByTitle("You — tester");
    fireEvent.click(identity);
    // Prevent the native dialog's Escape default after dismissing its menu.
    expect(fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" })).toBe(false);
    expect(screen.queryByRole("menu")).toBeNull();
    expect(screen.getByRole("dialog", { name: "Navigation" })).toBeTruthy();
    expect(document.activeElement).toBe(identity);
  });

  it.each(["popstate", "skein-peek"])("closes the native top layer before a %s task-panel handoff", (eventName) => {
    state.desktop = false;
    render(<Nav />);
    fireEvent.click(screen.getByRole("button", { name: "Open navigation" }));
    const dialog = screen.getByRole("dialog", { name: "Navigation" }) as HTMLDialogElement;
    window.history.pushState({}, "", "/artifacts?task=12");
    act(() => {
      window.dispatchEvent(new Event(eventName));
      // The top layer must be closed in the history event, before TaskPeek's
      // effect tries to focus a body sibling outside this native dialog.
      expect(dialog.open).toBe(false);
    });
    expect(document.body.style.overflow).toBe("");
  });

  it("hands the mobile drawer to Search without opening Capture", () => {
    state.desktop = false;
    render(<Nav />);
    fireEvent.click(screen.getByRole("button", { name: "Open navigation" }));
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    expect(screen.queryByRole("dialog", { name: "Navigation" })).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole("textbox", { name: "Search Skein" }));
    expect(document.body.style.overflow).toBe("");
  });

  it("keeps mobile labels and children after a saved desktop collapse", () => {
    state.desktop = false;
    localStorage.setItem("skein-navigation-collapsed", "true");
    render(<Nav />);
    fireEvent.click(screen.getByRole("button", { name: "Open navigation" }));
    expect(screen.getByRole("link", { name: "Reports" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Expand navigation" })).toBeNull();
  });

  it("opens a labelled mobile dialog and closes it for the auth gate", () => {
    state.desktop = false;
    render(<Nav />);
    fireEvent.click(screen.getByRole("button", { name: "Open navigation" }));
    const dialog = screen.getByRole("dialog", { name: "Navigation" });
    expect(within(dialog).getByRole("link", { name: "Reports" })).toBeTruthy();
    expect(document.body.style.overflow).toBe("hidden");
    act(() => setGated(true));
    expect(screen.queryByRole("dialog", { name: "Navigation" })).toBeNull();
    expect(document.body.style.overflow).toBe("");
  });
});
