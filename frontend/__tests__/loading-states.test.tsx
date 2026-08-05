import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

/** A list page has THREE states, not two. Starting from [] makes "still
 *  loading", "nothing here", and "the load failed" render identically —
 *  and the empty state is a claim ("nothing is waiting on you"), so showing
 *  it before the answer arrives is a lie the reader acts on. Review, Intake
 *  and Charter all shipped that way; portfolio-loading.test.tsx pins the
 *  same rule for the card version. */

const mode = { fail: false };
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: () =>
      mode.fail
        ? Promise.reject(new Error("queue service exploded"))
        : new Promise(() => {}), // never settles: the page stays mid-load
    getUser: () => "tester",
  };
});

vi.mock("next/navigation", () => ({ usePathname: () => "/review" }));

import ReviewPage from "@/app/review/page";
import IntakePage from "@/app/intake/page";
import CharterPage from "@/app/charter/page";

const PAGES: [string, () => React.ReactElement, RegExp][] = [
  ["Approvals", () => <ReviewPage />, /When agents .* propose changes|Loading…/],
  ["Requests", () => <IntakePage />, /No requests yet|Loading…/],
  ["Charter", () => <CharterPage />, /No charter entries yet|Loading…/],
];

describe("a list page mid-load", () => {
  for (const [name, Page] of PAGES) {
    it(`${name} says Loading, not an empty queue`, () => {
      render(Page());
      expect(screen.getAllByText("Loading…").length).toBeGreaterThan(0);
      // the empty-state claims must not appear before the answer arrives
      expect(screen.queryByText(/No requests yet/)).toBeNull();
      expect(screen.queryByText(/No charter entries yet/)).toBeNull();
      expect(screen.queryByText(/nothing is waiting/i)).toBeNull();
    });
  }
});

describe("a list page whose load failed", () => {
  for (const [name, Page] of PAGES) {
    it(`${name} reports the failure and stops claiming to load`, async () => {
      mode.fail = true;
      try {
        render(Page());
        expect(await screen.findByText(/queue service exploded/)).toBeTruthy();
        expect(screen.queryByText("Loading…")).toBeNull();
      } finally {
        mode.fail = false;
      }
    });
  }
});
