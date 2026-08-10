import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { Suspense } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/** The engagement brief must not drop what the API sent it.
 *
 *  The page exists to answer "how is this going", and the answer is the health
 *  RECEIPTS — the sentences naming the overdue milestone and the escalated
 *  blocker. A first cut fetched them, converted their references to links, and
 *  rendered a coloured dot instead. This pins that they reach the screen, that
 *  their references resolve, and that the empty states say what they know
 *  rather than asserting a fact.
 */

const brief = {
  engagement: {
    id: 3,
    name: "Atlas",
    project_class: "migration",
    kind: "delivery",
    status: "active",
    lead: "ava",
    outcome: "Cut over without a read outage.",
    timebox_end: null,
    kill_criteria: null,
    conclusion: null,
  },
  health: {
    color: "red",
    receipts: [
      {
        message: "milestone #4 'Cutover' overdue since 2026-08-01",
        refs: [{ entity: "milestone", id: 4 }],
      },
    ],
    moved_from: "yellow",
  },
  milestones: [{ id: 4, title: "Cutover", status: "planned", due_date: "2026-08-01" }],
  tasks: [],
  blockers: [],
  delegated: [],
  lessons: [],
  artifacts: [],
  plan_diff: {},
  next_actions: [],
  queue_scanned: 50,
};

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string) =>
      path.includes("/brief") ? Promise.resolve(brief) : Promise.resolve({}),
  };
});

vi.mock("next/navigation", () => ({ usePathname: () => "/engagement/3" }));

import EngagementBrief from "@/app/engagement/[id]/page";

// created once, outside render: `use()` re-suspends on a promise it has not
// seen, so a new one per render never settles
const PARAMS = Promise.resolve({ id: "3" });

describe("the engagement brief", () => {
  // the Suspense boundary the App Router supplies in the real app
  const mount = () =>
    render(
      <Suspense fallback={null}>
        <EngagementBrief params={PARAMS} />
      </Suspense>,
    );

  // One throwaway mount first. `use()` suspends on a promise React has not yet
  // seen settle, and that resolution does not flush inside RTL's act window —
  // so without this the FIRST test in the file commits nothing while every
  // later one passes, which is a suite that proves the wrong thing.
  beforeEach(async () => {
    mount();
    await PARAMS;
    cleanup();
  });

  const page = mount;

  it("resolves the references inside a health receipt", async () => {
    page();
    // the sentence is split into runs by design — `milestone #4` becomes a
    // link and the rest stays text (components/receipt.tsx) — so the assertion
    // is on the link the split produces, which is the point of shipping refs
    const link = await waitFor(() =>
      screen.getByRole("link", { name: "milestone #4" }),
    );
    expect(link.getAttribute("href")).toBe("/dashboard#milestones");
  });

  it("says the colour in words, not only in hue", async () => {
    page();
    // hue is the entire payload for a sighted reader, including anyone with a
    // colour deficiency
    await waitFor(() => expect(screen.getByText("red")).toBeTruthy());
  });

  it("says what it checked, not that nothing is wrong", async () => {
    page();
    // "Nothing is escalated, overdue, or unowned" would be a claim about the
    // world: the queue is ranked portfolio-wide and narrowed afterward, so the
    // card can only speak for the queue it read
    await waitFor(() =>
      expect(
        screen.getByText(/Nothing in the portfolio queue belongs/),
      ).toBeTruthy(),
    );
  });

  it("keeps the health signal out of the page title", async () => {
    page();
    // heading navigation must not announce mutable state as part of the title
    const h1 = await waitFor(() => screen.getByRole("heading", { level: 1 }));
    expect(h1.textContent).toBe("Atlas");
  });
});
