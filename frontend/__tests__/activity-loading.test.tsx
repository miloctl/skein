import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

/** The activity ledger is the largest read in the app. This page was the one
 *  the three-state sweep missed: before the fetch settled it rendered no
 *  rows, no empty state and no spinner — a blank screen for the longest
 *  load in the product. loading-states.test.tsx pins the same rule for the
 *  list pages. */

const mode = { fail: false };
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: () =>
      mode.fail
        ? Promise.reject(new Error("ledger service exploded"))
        : new Promise(() => {}), // never settles: the page stays mid-load
    getUser: () => "tester",
  };
});
vi.mock("next/navigation", () => ({ usePathname: () => "/activity" }));

import ActivityPage from "@/app/activity/page";

const EMPTY_CLAIM = /Nothing on the ledger yet/;

describe("the Activity page mid-load", () => {
  it("says Loading, and does not claim the ledger is empty", () => {
    render(<ActivityPage />);
    expect(screen.getByText("Loading…")).toBeTruthy();
    expect(screen.queryByText(EMPTY_CLAIM)).toBeNull();
  });
});

describe("the Activity page whose load failed", () => {
  it("reports the failure and stops claiming to load", async () => {
    mode.fail = true;
    try {
      render(<ActivityPage />);
      expect(await screen.findByText(/ledger service exploded/)).toBeTruthy();
      expect(screen.queryByText("Loading…")).toBeNull();
      // a failed load is not an empty ledger
      expect(screen.queryByText(EMPTY_CLAIM)).toBeNull();
    } finally {
      mode.fail = false;
    }
  });
});
