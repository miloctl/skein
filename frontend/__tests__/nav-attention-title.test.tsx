import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** The attention count in the tab title, and what the nav does while the auth
 *  gate stands. The immediate notification tier reaches a person who is in
 *  their editor only through the tab, so the title is the delivery half of a
 *  signal the product already computes.
 *
 *  The MutationObserver is the part a later edit breaks: Next re-applies the
 *  route's metadata title on navigation, and a plain assignment loses the
 *  count depending on which effect ran last. One test here fails if someone
 *  simplifies it back to `document.title = ...`. */

const count = { value: 0 };
// held distinct from `yours` so the badge and the title cannot be confused
const INBOX = 7;

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    // shaped like the real endpoints: the ?task= test mounts the peek panel,
    // and a body of the wrong shape crashes its render before the effect
    // under test can commit
    api: (path: string) => {
      // three DISTINCT numbers. `yours` is what the title carries and
      // `inbox` is what the nav badge carries; `count` is the compatibility
      // alias the CLI reads. Equal values here would let a component that
      // reads the wrong field pass every assertion by coincidence.
      if (path.startsWith("/api/attention"))
        return Promise.resolve({ count: 0, yours: count.value, inbox: INBOX });
      if (path.endsWith("/worklog")) return Promise.resolve([]);
      if (path.startsWith("/api/agents")) return Promise.resolve([]);
      if (path.startsWith("/api/tasks/"))
        return Promise.resolve({
          id: 12,
          title: "Wire the gate",
          status: "doing",
          priority: "normal",
        });
      return Promise.resolve({});
    },
    authConfig: () => Promise.resolve({ mode: "trusted-header" }),
  };
});

vi.mock("next/navigation", () => ({ usePathname: () => "/dashboard" }));

import { Nav } from "@/components/nav";
import { TaskPeek } from "@/components/task-peek";
import { setGated } from "@/lib/gated";

beforeEach(() => {
  document.title = "Skein";
  window.history.pushState({}, "", "/dashboard");
});
afterEach(() => {
  count.value = 0;
  setGated(false);
});

describe("the tab title", () => {
  it("carries the count when work is waiting", async () => {
    count.value = 3;
    render(<Nav />);
    await waitFor(() => expect(document.title).toBe("(3) Skein"));
  });

  it("stays clean at zero — an empty inbox must not look like one item", async () => {
    count.value = 0;
    render(<Nav />);
    await waitFor(() => expect(document.title).toBe("Skein"));
  });

  it("drops the count while the auth gate stands", async () => {
    // a session that expires mid-task keeps its last number, so a locked-out
    // reader sat in front of a tab promising three things to do at a
    // workspace that would not open
    count.value = 3;
    render(<Nav />);
    await waitFor(() => expect(document.title).toBe("(3) Skein"));
    act(() => setGated(true));
    await waitFor(() => expect(document.title).toBe("Skein"));
  });

  it("never stacks prefixes when the title is rewritten", async () => {
    count.value = 2;
    render(<Nav />);
    await waitFor(() => expect(document.title).toBe("(2) Skein"));
    // what a route change does: the metadata title lands on top of ours
    document.title = "Skein";
    await waitFor(() => expect(document.title).toBe("(2) Skein"));
    expect(document.title.match(/\(/g)?.length).toBe(1);
  });
});

describe("the nav under the auth gate", () => {
  /** The gate REPLACES the page but only COVERS the nav, so without inert a
   *  keyboard user tabs into links hidden under the overlay and focus lands
   *  on nothing visible. */
  it("goes inert while the gate stands, and comes back after", async () => {
    const { container } = render(<Nav />);
    const header = container.querySelector("header") as HTMLElement;
    expect(header.hasAttribute("inert")).toBe(false);
    act(() => setGated(true));
    await waitFor(() => expect(header.hasAttribute("inert")).toBe(true));
    act(() => setGated(false));
    await waitFor(() => expect(header.hasAttribute("inert")).toBe(false));
  });

  it("stays inert when a ?task= panel gives the attribute back", async () => {
    // The real sequence, and the one that broke twice: a digest link carries
    // ?task=12, TaskPeek opens BEFORE the auth mode is known and inerts every
    // body sibling, then the gate goes up. In that one commit React runs
    // TaskPeek's cleanup — which removes inert from the nav — before any
    // effect body. As a rendered inert={gated} prop the nav's inert was set
    // during the same commit and then stripped, and React never re-applies an
    // attribute it believes is already set, so the nav stayed reachable under
    // the gate for the rest of the session. Rendered into document.body so
    // the two are siblings, which is what TaskPeek walks.
    window.history.pushState({}, "", "/dashboard?task=12");
    render(
      <>
        <Nav />
        <TaskPeek />
      </>,
      { container: document.body },
    );
    const header = document.querySelector("header") as HTMLElement;
    await waitFor(() => expect(header.hasAttribute("inert")).toBe(true)); // TaskPeek's
    act(() => setGated(true));
    await waitFor(() => expect(document.querySelector('[role="dialog"]')).toBeNull());
    expect(header.hasAttribute("inert")).toBe(true);
  });
});

describe("navigation labels", () => {
  it("uses the visible capture action as its accessible name", () => {
    render(<Nav />);
    expect(
      screen.getByRole("button", { name: /^\+ Capture$/ }),
    ).toBeTruthy();
  });
});

describe("the Inbox badge", () => {
  it("carries the shared queue, not the personal number", async () => {
    // The two numbers answer different questions and reached the page as one.
    // The badge sits on Inbox and must promise only what that page shows;
    // the title says "waiting on you" and must not count a queue anyone works.
    count.value = 3;
    render(<Nav />);
    const inbox = await waitFor(() => {
      const el = document.querySelector('a[href="/review"]');
      if (!el?.textContent?.includes(String(INBOX))) throw new Error("not yet");
      return el;
    });
    expect(inbox.textContent).toContain(String(INBOX));
    expect(document.title).toBe("(3) Skein");
  });
});
