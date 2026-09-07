import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** The peek's accessibility contract, pinned because none of it is caught by
 *  axe — the panel passed an automated sweep clean while a screen reader user
 *  could not read the task list at all.
 *
 *  Each test here corresponds to a real defect found by audit:
 *  an aria-label that deleted the task title from the accessibility tree,
 *  aria-modal="true" over a background that stayed tabbable, and focus
 *  restoration that dropped to <body> when the trigger had unmounted. */

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string) =>
      path.includes("worklog")
        ? Promise.resolve([])
        : Promise.resolve({
            id: 4,
            title: "Build the happy path",
            status: "todo",
            priority: "high",
          }),
  };
});

import { NavSearch } from "@/components/nav-search";
import { PeekLink, TaskPeek } from "@/components/task-peek";

beforeEach(() => window.history.replaceState({}, "", "/"));
afterEach(() => vi.restoreAllMocks());

describe("the panel before its first open", () => {
  it.each([false, true])("does not move existing focus (Strict Mode: %s)", (strict) => {
    render(
      <>
        <a href="#content">Skip to content</a>
        <NavSearch />
        <button>Page control</button>
      </>,
    );
    const control = screen.getByRole("button", { name: "Page control" });
    control.focus();
    const dispatch = vi.spyOn(window, "dispatchEvent");

    render(
      strict ? <StrictMode><TaskPeek /></StrictMode> : <TaskPeek />,
    );
    act(() => window.dispatchEvent(new PopStateEvent("popstate")));

    expect(document.activeElement).toBe(control);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(dispatch.mock.calls.filter(([event]) => event.type === "skein-peek-close")).toEqual([]);
  });

  it("leaves focus at the document start before the skip link", () => {
    render(
      <>
        <a href="#content">Skip to content</a>
        <NavSearch />
        <TaskPeek />
      </>,
    );

    // jsdom has no native Tab navigation. Keeping body focused leaves the
    // skip link first in the browser's tab order (app/layout.tsx).
    expect(document.activeElement).toBe(document.body);
    expect(screen.getByRole("link", { name: "Skip to content" }).tabIndex).toBe(0);
  });
});

describe("a task link", () => {
  it("keeps the task title in its accessible name", () => {
    render(<PeekLink taskId={4}>#4 Build the happy path</PeekLink>);
    // an aria-label REPLACES the subtree — with one, this query finds nothing
    // and a voice-control user cannot say what they can see
    expect(
      screen.getByRole("button", { name: /Build the happy path/ }),
    ).toBeTruthy();
  });

  it("still says what activating it does", () => {
    render(<PeekLink taskId={4}>#4 Build the happy path</PeekLink>);
    expect(screen.getByRole("button", { name: /^Open/ })).toBeTruthy();
  });
});

describe("the open panel", () => {
  it("makes the background inert, because aria-modal does not", async () => {
    // aria-modal prunes the screen reader buffer and leaves Tab order alone,
    // so without this a keyboard user walks out of the panel into content
    // their reader was told does not exist
    const outside = document.createElement("div");
    outside.innerHTML = `<button>background control</button>`;
    document.body.appendChild(outside);

    window.history.pushState({}, "", "?task=4");
    render(<TaskPeek />);
    await waitFor(() =>
      expect(screen.getByRole("dialog")).toBeTruthy(),
    );
    expect(outside.hasAttribute("inert")).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: /Close/ }));
    await waitFor(() => expect(outside.hasAttribute("inert")).toBe(false));
    outside.remove();
  });
});

describe("focus on close", () => {
  it.each(["Close", "Escape", "scrim"])("keeps the panel open until URL sync after %s", async (method) => {
    const returnUrl = "/dashboard?view=open#content";
    window.history.replaceState({}, "", returnUrl);
    const background = render(
      <main id="content" tabIndex={-1}>
        <PeekLink taskId={4}>#4 Build the happy path</PeekLink>
      </main>,
    );
    render(<TaskPeek />);
    const trigger = screen.getByRole("button", { name: /Build the happy path/ });
    trigger.focus();
    fireEvent.click(trigger);
    await screen.findByRole("dialog");
    const close = screen.getByRole("button", { name: /Close/ });
    const dispatch = vi.spyOn(window, "dispatchEvent");
    // Hold traversal at the History API boundary. jsdom cannot reproduce the
    // browser's fragment focus after history.back() returns.
    const back = vi.spyOn(window.history, "back").mockImplementation(() => {});
    const frames: FrameRequestCallback[] = [];
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => frames.push(callback));
    const receipts = () => dispatch.mock.calls
      .filter(([event]) => event.type === "skein-peek-close")
      .map(([event]) => (event as CustomEvent).detail);

    if (method === "Close") fireEvent.click(close);
    else if (method === "Escape") fireEvent.keyDown(document, { key: "Escape" });
    else fireEvent.mouseDown(screen.getByRole("dialog").parentElement!);

    expect(back).toHaveBeenCalledOnce();
    expect(window.location.search).toBe("?view=open&task=4");
    expect(window.location.hash).toBe("#content");
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(document.activeElement).toBe(close);
    expect(background.container.hasAttribute("inert")).toBe(true);
    expect(receipts()).toEqual([]);

    if (method === "Close") fireEvent.click(close);
    else if (method === "Escape") fireEvent.keyDown(document, { key: "Escape", repeat: true });
    else fireEvent.mouseDown(screen.getByRole("dialog").parentElement!);
    expect(back).toHaveBeenCalledOnce();

    act(() => {
      window.history.replaceState({}, "", returnUrl);
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(receipts()).toEqual([]);
    // Native fragment focus follows popstate and the React close commit.
    screen.getByRole("main").focus();
    act(() => frames[0]?.(0));
    expect(document.activeElement).toBe(trigger);
    expect(background.container.hasAttribute("inert")).toBe(false);
    expect(window.location.pathname + window.location.search + window.location.hash).toBe(returnUrl);
    expect(receipts()).toEqual([{ taskId: 4 }]);
    act(() => window.dispatchEvent(new PopStateEvent("popstate")));
    expect(receipts()).toEqual([{ taskId: 4 }]);
  });

  it.each(["reopen", "unmount"])("cancels stale close focus on %s", async (next) => {
    const frames = new Map<number, FrameRequestCallback>();
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
      frames.set(1, callback);
      return 1;
    });
    vi.spyOn(window, "cancelAnimationFrame").mockImplementation((id) => { frames.delete(id); });
    render(<><PeekLink taskId={4}>#4 Build the happy path</PeekLink><button>Page control</button></>);
    const panel = render(<TaskPeek />);
    const trigger = screen.getByRole("button", { name: /Build the happy path/ });
    trigger.focus();
    fireEvent.click(trigger);
    await screen.findByRole("dialog");
    const dispatch = vi.spyOn(window, "dispatchEvent");
    act(() => {
      window.history.replaceState({}, "", "/");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(frames.size).toBe(1);

    if (next === "reopen") {
      trigger.focus();
      fireEvent.click(trigger);
      await screen.findByRole("dialog");
    } else {
      panel.unmount();
      screen.getByRole("button", { name: "Page control" }).focus();
    }
    act(() => frames.forEach((callback) => callback(0)));
    expect(document.activeElement).toBe(screen.getByRole("button", {
      name: next === "reopen" ? "Close the task panel" : "Page control",
    }));
    expect(dispatch.mock.calls.filter(([event]) => event.type === "skein-peek-close")).toEqual([]);
  });

  it.each(["Close", "Escape", "Back", "scrim"])("returns focus to the trigger after %s and Forward", async (method) => {
    const dispatch = vi.spyOn(window, "dispatchEvent");
    render(
      <>
        <NavSearch />
        <PeekLink taskId={4}>#4 Build the happy path</PeekLink>
        <TaskPeek />
      </>,
    );
    const trigger = screen.getByRole("button", { name: /Build the happy path/ });
    trigger.focus();
    fireEvent.click(trigger);
    await screen.findByRole("dialog");
    expect(document.activeElement).toBe(screen.getByRole("button", { name: /Close/ }));
    if (method === "Close") fireEvent.click(screen.getByRole("button", { name: /Close/ }));
    else if (method === "Escape") fireEvent.keyDown(document, { key: "Escape" });
    else if (method === "scrim") fireEvent.mouseDown(screen.getByRole("dialog").parentElement!);
    else act(() => window.history.back());

    await waitFor(() => expect(window.location.search).toBe(""));
    expect(screen.queryByRole("dialog")).toBeNull();
    await waitFor(() => expect(document.activeElement).toBe(trigger));
    expect(dispatch.mock.calls.filter(([event]) => event.type === "skein-peek-close").map(([event]) => (event as CustomEvent).detail)).toEqual([{ taskId: 4 }]);

    act(() => window.history.forward());
    await screen.findByRole("dialog");
    expect(document.activeElement).toBe(screen.getByRole("button", { name: /Close/ }));
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(window.location.search).toBe(""));
    await waitFor(() => expect(document.activeElement).toBe(trigger));
    expect(dispatch.mock.calls.filter(([event]) => event.type === "skein-peek-close").map(([event]) => (event as CustomEvent).detail)).toEqual([{ taskId: 4 }, { taskId: 4 }]);
  });

  it.each(["Close", "Escape", "scrim"])("closes an unmarked direct link in place with %s", async (method) => {
    window.history.replaceState({}, "", "/dashboard?view=open&task=4#content");
    const back = vi.spyOn(window.history, "back").mockImplementation(() => {});
    const dispatch = vi.spyOn(window, "dispatchEvent");
    render(<><NavSearch /><TaskPeek /></>);
    await screen.findByRole("dialog");

    if (method === "Close") fireEvent.click(screen.getByRole("button", { name: /Close/ }));
    else if (method === "Escape") fireEvent.keyDown(document, { key: "Escape" });
    else fireEvent.mouseDown(screen.getByRole("dialog").parentElement!);

    expect(back).not.toHaveBeenCalled();
    expect(window.location.pathname + window.location.search + window.location.hash)
      .toBe("/dashboard?view=open#content");
    await waitFor(() => expect(document.activeElement).toBe(screen.getByLabelText("Search Skein")));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(dispatch.mock.calls.filter(([event]) => event.type === "skein-peek-close")
      .map(([event]) => (event as CustomEvent).detail)).toEqual([{ taskId: 4 }]);
  });

  it.each(["body", "opener"])("opens a deep link and restores focus from %s", async (target) => {
    render(
      <>
        <NavSearch />
        <button>Page control</button>
      </>,
    );
    const control = screen.getByRole("button", { name: "Page control" });
    if (target === "opener") control.focus();
    else expect(document.activeElement).toBe(document.body);
    window.history.pushState({}, "", "?task=4");

    render(<TaskPeek />);
    await screen.findByRole("dialog");
    expect(document.activeElement).toBe(screen.getByRole("button", { name: /Close/ }));
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(window.location.search).toBe(""));
    await waitFor(() => expect(document.activeElement).toBe(target === "opener" ? control : screen.getByLabelText("Search Skein")));
  });

  it("falls back to the search box when the trigger has unmounted", async () => {
    // The search dropdown closes on activation, so the row that opened the
    // panel is DETACHED by the time focus is restored — and .focus() on a
    // detached node silently no-ops, dropping the reader on <body>, which is
    // the exact failure the restore ref exists to prevent.
    window.history.pushState({}, "", "/");
    const search = document.createElement("input");
    search.id = "nav-search";
    document.body.appendChild(search);

    const holder = document.createElement("div");
    document.body.appendChild(holder);
    const { unmount } = render(<PeekLink taskId={4}>#4 gone soon</PeekLink>, {
      container: holder,
    });
    render(<TaskPeek />);

    const trigger = screen.getByRole("button", { name: /gone soon/ });
    trigger.focus();
    fireEvent.click(trigger);
    await waitFor(() => expect(screen.getByRole("dialog")).toBeTruthy());
    unmount(); // the dropdown closes and takes its row with it

    fireEvent.click(screen.getByRole("button", { name: /Close/ }));
    await waitFor(() => expect(document.activeElement).toBe(search));
    expect(document.activeElement).not.toBe(document.body);
    search.remove();
    holder.remove();
  });
});
