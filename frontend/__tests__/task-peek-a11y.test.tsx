import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

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

import { PeekLink, TaskPeek } from "@/components/task-peek";

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
  it("returns focus to the trigger", async () => {
    window.history.pushState({}, "", "/");
    const { container } = render(
      <>
        <PeekLink taskId={4}>#4 Build the happy path</PeekLink>
        <TaskPeek />
      </>,
    );
    const trigger = screen.getByRole("button", { name: /Build the happy path/ });
    trigger.focus();
    fireEvent.click(trigger);
    await waitFor(() => expect(screen.getByRole("dialog")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /Close/ }));
    await waitFor(() => expect(document.activeElement).toBe(trigger));
    expect(container).toBeTruthy();
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
