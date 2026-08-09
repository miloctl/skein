import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/** Reports is a master/detail pair driven by `?id=`, so the two ways to change
 *  the selection — clicking a row and pressing Back — must agree. They did not:
 *  the newest report opened without writing the URL, so the first history entry
 *  was a bare /artifacts and Back left the pane loading forever with nothing
 *  selected. */

const BASE = { engagement_id: null, kind: "digest", title: "Digest", path: "/d.md", created_by: "scheduler", created_at: "2026-08-09T07:00:00+00:00" };

const ROWS = [
  { id: 7, engagement_id: null, kind: "digest", title: "Digest 2026-08-09", path: "/d/7.md", created_by: "scheduler", created_at: "2026-08-09T07:00:00+00:00" },
  { id: 6, engagement_id: null, kind: "ritual", title: "Week open 2026-08-05", path: "/d/6.md", created_by: "ava", created_at: "2026-08-05T06:30:00+00:00" },
];

const ORIGINAL = [...ROWS];

const mode = { failList: false };

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string) => {
      if (path === "/api/artifacts")
        return mode.failList
          ? Promise.reject(new Error("artifacts service exploded"))
          : Promise.resolve(ROWS);
      const id = Number(path.split("/").pop());
      const row = ROWS.find((r) => r.id === id);
      return Promise.resolve({ ...row, markdown: `# Body of ${id}` });
    },
  };
});

vi.mock("next/navigation", () => ({ usePathname: () => "/artifacts" }));

import ArtifactsPage from "@/app/artifacts/page";

beforeEach(() => {
  mode.failList = false;
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

  it("labels the count as a page once the read is capped", async () => {
    // list_artifacts returns ORDER BY id DESC LIMIT 50, and the digest files
    // one a day — past the cap the length is a page, not a total
    // EXACTLY the cap: list_artifacts is `ORDER BY id DESC LIMIT 50`, so 50 is
    // the only length that means "there may be more". At 52 the assertion also
    // passes with `>` instead of `>=`, and 52 is a length no code path emits.
    ROWS.length = 0;
    ROWS.push(...Array.from({ length: 50 }, (_, i) => ({ ...BASE, id: 100 + i })));
    try {
      render(<ArtifactsPage />);
      await waitFor(() => expect(screen.getByText(/Reports \(newest/)).toBeTruthy());
    } finally {
      ROWS.length = 0;
      ROWS.push(...ORIGINAL);
    }
  });
});
