import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

/** Browse hides done tasks, so a merge that closes a task removed it and its
 *  forge link in the same second — the receipt for the most satisfying moment
 *  in the loop. The Recently shipped section is the surface that keeps it.
 *
 *  Pins three things a later edit gets wrong: the window is bounded (old work
 *  must not accumulate here forever), the forge link travels with the row,
 *  and the section does not depend on the commitment line — Health's week
 *  plan lists done work only when it was committed to a week. */

const DAY = 86_400_000;
const iso = (daysAgo: number) =>
  new Date(Date.now() - daysAgo * DAY).toISOString();

const rows = {
  tasks: [
    {
      id: 1,
      title: "shipped yesterday",
      status: "done",
      completed_at: iso(1),
      forge_url: "https://forge.example/pr/9",
    },
    // 8 days: the boundary. At 30 the test passed with the window widened
    // to 14 or 21, which is most of the ways this constant gets edited.
    {
      id: 2,
      title: "shipped just outside the window",
      status: "done",
      completed_at: iso(8),
    },
    { id: 3, title: "still open", status: "in_progress", completed_at: null },
    // done, but the forge never told us when — undated work cannot be placed
    // in a 7-day window, and guessing "recent" would invent a ship date
    { id: 4, title: "done with no date", status: "done", completed_at: null },
  ],
};

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string) => {
      if (path === "/api/tasks/browse")
        return Promise.resolve({
          open: rows.tasks.filter((row) => row.status !== "done"),
          done: rows.tasks.filter((row) => row.status === "done"),
        });
      const key = path.replace("/api/", "").split("?")[0];
      return Promise.resolve(key === "pulse" ? null : []);
    },
  };
});

vi.mock("next/navigation", () => ({ usePathname: () => "/dashboard" }));

import Dashboard from "@/app/dashboard/page";

describe("the Recently shipped section", () => {
  it("shows work finished inside the window, with its code link", async () => {
    render(<Dashboard />);
    expect(await screen.findByText("shipped yesterday")).toBeTruthy();
    const link = screen.getByRole("link", {
      name: /Code for task #1/,
    }) as HTMLAnchorElement;
    expect(link.href).toBe("https://forge.example/pr/9");
  });

  it("drops work older than the window, and undated done work", async () => {
    render(<Dashboard />);
    await screen.findByText("shipped yesterday");
    expect(screen.queryByText("shipped just outside the window")).toBeNull();
    expect(screen.queryByText("done with no date")).toBeNull();
  });

  it("leaves open work to the Tasks section", async () => {
    render(<Dashboard />);
    await screen.findByText("shipped yesterday");
    // present on the page (Tasks renders it) but exactly once — a done task
    // must not be listed by both sections
    expect(screen.getAllByText("still open").length).toBe(1);
    expect(screen.getAllByText("shipped yesterday").length).toBe(1);
  });
});
