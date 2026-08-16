import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/** Reports is a master/detail pair driven by `?id=`, so the two ways to change
 *  the selection — clicking a row and pressing Back — must agree. They did not:
 *  the newest report opened without writing the URL, so the first history entry
 *  was a bare /artifacts and Back left the pane loading forever with nothing
 *  selected. */

const ROWS = [
  { id: 7, engagement_id: null, kind: "digest", title: "Digest 2026-08-09", path: "/d/7.md", created_by: "scheduler", created_at: "2026-08-09T07:00:00+00:00" },
  { id: 6, engagement_id: null, kind: "ritual", title: "Week open 2026-08-05", path: "/d/6.md", created_by: "ava", created_at: "2026-08-05T06:30:00+00:00" },
];

const OLDER = [
  { id: 5, engagement_id: null, kind: "digest", title: "Digest 2026-08-04", path: "/d/5.md", created_by: "scheduler", created_at: "2026-08-04T07:00:00+00:00" },
];

const THREADS = [
  { entity: "task", id: 12 },
  { entity: "decision", id: 4 },
  { entity: "proposal", id: 8 },
  { entity: "unknown", id: 9 },
];

const mode = { failList: false, hasOlder: false };

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string) => {
      if (path === "/api/artifacts/page")
        return mode.failList
          ? Promise.reject(new Error("artifacts service exploded"))
          : Promise.resolve({ items: ROWS, next_before: mode.hasOlder ? 6 : null });
      if (path.startsWith("/api/artifacts/page?before="))
        return Promise.resolve({ items: OLDER, next_before: null });
      const id = Number(path.split("/").pop());
      const row = [...ROWS, ...OLDER].find((r) => r.id === id);
      return Promise.resolve({
        ...row,
        markdown: `# Body of ${id}\n\nBare #99 stays text.`,
        threads: id === 7 ? THREADS : [],
      });
    },
  };
});

vi.mock("next/navigation", () => ({ usePathname: () => "/artifacts" }));

import ArtifactsPage from "@/app/artifacts/page";

beforeEach(() => {
  mode.failList = false;
  mode.hasOlder = false;
  window.history.replaceState({}, "", "/artifacts");
});

describe("the Reports page", () => {
  it("names the auto-opened report in the URL, so Back has somewhere to return to", async () => {
    render(<ArtifactsPage />);
    await screen.findByText("Body of 7");
    // replaceState, not push: the first entry already carries the selection
    expect(new URL(window.location.href).searchParams.get("id")).toBe("7");
    expect(window.history.length).toBeGreaterThan(0);
  });

  it("renders the report a ?id= link names, not the newest", async () => {
    window.history.replaceState({}, "", "/artifacts?id=6");
    render(<ArtifactsPage />);
    expect(await screen.findByText("Body of 6")).toBeTruthy();
  });

  it("shows one state when the list fails: the failure, not also Loading", async () => {
    mode.failList = true;
    render(<ArtifactsPage />);
    await screen.findByText(/artifacts service exploded/);
    // the empty state is a verdict about data that never arrived…
    expect(screen.queryByText(/No report yet/)).toBeNull();
    // …and "Loading…" beside the failure leaves the reader waiting for a list
    // that is never coming
    expect(screen.queryByText("Loading…")).toBeNull();
  });

  it("appends older reports without changing the open report or URL", async () => {
    mode.hasOlder = true;
    render(<ArtifactsPage />);
    await screen.findByText("Body of 7");

    fireEvent.click(screen.getByRole("button", { name: "Older reports" }));

    expect(await screen.findByText("Digest 2026-08-04")).toBeTruthy();
    expect(screen.getByText("Body of 7")).toBeTruthy();
    expect(new URL(window.location.href).searchParams.get("id")).toBe("7");
    expect(screen.queryByRole("button", { name: "Older reports" })).toBeNull();
  });

  it("links explicit typed threads without changing the report body", async () => {
    render(<ArtifactsPage />);

    expect(
      await screen.findByRole("heading", { level: 3, name: "Threads in this report" }),
    ).toBeTruthy();
    const task = screen.getByText("task #12");
    expect(task.closest("button")).toBeTruthy();
    expect(screen.getByRole("link", { name: "decision #4" }).getAttribute("href")).toBe(
      "/charter#charter-entry-4",
    );
    expect(screen.getByRole("link", { name: "proposal #8" }).getAttribute("href")).toBe(
      "/review?id=8",
    );
    const unknown = screen.getByText("unknown #9");
    expect(unknown.closest("a, button")).toBeNull();
    expect(screen.getByText("Bare #99 stays text.").closest("a, button")).toBeNull();

    fireEvent.click(task.closest("button") as HTMLElement);
    expect(new URL(window.location.href).searchParams.get("task")).toBe("12");
    expect(screen.getByText("Body of 7")).toBeTruthy();
  });

  it("does not show a thread section when the report has no references", async () => {
    window.history.replaceState({}, "", "/artifacts?id=6");
    render(<ArtifactsPage />);

    await screen.findByText("Body of 6");
    expect(screen.queryByRole("heading", { name: "Threads in this report" })).toBeNull();
  });
});


