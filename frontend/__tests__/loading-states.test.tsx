import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

/** A list page has THREE states, not two. Starting from [] makes "still
 *  loading", "nothing here", and "the load failed" render identically —
 *  and the empty state is a claim ("nothing is waiting on you"), so showing
 *  it before the answer arrives OR next to a failure is a lie the reader
 *  acts on. Review, Intake and Charter all shipped that way;
 *  portfolio-loading.test.tsx pins the same rule for the card version. */

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

// each page's empty state, matched by its FIXED sentence (Review's headline
// is whimsy-pool text that varies per render, so match the stable line under it)
const PAGES: [string, () => React.ReactElement, RegExp][] = [
  ["Approvals", () => <ReviewPage />, /propose changes, they wait here/],
  ["Requests", () => <IntakePage />, /No requests yet/],
  ["Charter", () => <CharterPage />, /No charter entries yet/],
];

describe("persistent authoring labels", () => {
  it("keeps Charter labels visible after the placeholders disappear", () => {
    render(<CharterPage />);
    expect(screen.getByText("Charter entry title", { selector: "label" })).toBeTruthy();
    expect(screen.getByText("Agreement", { selector: "label" })).toBeTruthy();
  });
});

describe("a list page mid-load", () => {
  for (const [name, Page, claim] of PAGES) {
    it(`${name} says Loading, not an empty queue`, () => {
      render(Page());
      expect(screen.getAllByText("Loading…").length).toBeGreaterThan(0);
      // the empty-state claim must not appear before the answer arrives
      expect(screen.queryByText(claim)).toBeNull();
    });
  }
});

describe("a list page whose load failed", () => {
  for (const [name, Page, claim] of PAGES) {
    it(`${name} reports the failure without claiming the queue is empty`, async () => {
      mode.fail = true;
      try {
        render(Page());
        // Approvals renders two failure lines (the queue and the Recently
        // approved section), so match all, not one
        expect((await screen.findAllByText(/queue service exploded/)).length).toBeGreaterThan(0);
        expect(screen.queryByText("Loading…")).toBeNull();
        // failure and "nothing here" are different answers — never both.
        // Charter shipped rendering its empty claim beside the error.
        expect(screen.queryByText(claim)).toBeNull();
      } finally {
        mode.fail = false;
      }
    });
  }
});
