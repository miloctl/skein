import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

/** "Needs you" has to mean it.
 *
 *  Every attention row landed under that one heading, including the team's
 *  intake queue and every pending proposal — work nobody had assigned to the
 *  reader. The heading was the product's daily habit, and a heading that
 *  overclaims teaches readers to skim past the rows that really are theirs.
 *  services/briefing.py labels each row with an audience; this pins that the
 *  page renders the two audiences apart, and counts only the personal ones.
 */

const briefing = {
  user: "tester",
  date: "2026-08-09",
  pending_reviews_total: 1,
  attention: [
    {
      kind: "blocker",
      ref_id: 3,
      group: "unblock",
      audience: "you",
      label: "blocker #3: Stuck on vendor",
      reason: "you own it",
      link: "/dashboard",
    },
    {
      kind: "intake",
      ref_id: 7,
      group: "decide",
      audience: "team",
      label: "intake #7: Need a thing",
      reason: "awaiting an accept, defer, or decline",
      link: "/intake",
    },
  ],
  your_work: { tasks: [], due_soon: [], standup_suggestion: "" },
  team: {
    recently_shipped: [],
    escalated_blockers: [],
    todays_events: [],
    recent_activity: [],
  },
};

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string) => {
      if (path.startsWith("/api/briefing")) return Promise.resolve(briefing);
      if (path.startsWith("/api/onboarding"))
        return Promise.resolve({ steps: [], done: true });
      if (path.startsWith("/api/field-guide/hint"))
        return Promise.resolve({
          suggestion: {
            id: "growth",
            feature: "Growth interests",
            pitch: "Name where you want to grow.",
            link: "/settings",
          },
        });
      return Promise.resolve({});
    },
  };
});

vi.mock("next/navigation", () => ({ usePathname: () => "/" }));

import MyDay from "@/app/page";

describe("My Day", () => {
  it("keeps the shared queues out of the count that says 'needs you'", async () => {
    render(<MyDay />);
    // one personal row, so the sentence is singular and names ONE thing —
    // with the intake row counted it read "2 things need you"
    await waitFor(() =>
      expect(screen.getByText(/1 thing needs you/)).toBeTruthy(),
    );
  });

  it("renders the team's queue under its own heading", async () => {
    render(<MyDay />);
    await waitFor(() => expect(screen.getByText("Team queues")).toBeTruthy());
    // the row itself is still shown — this is a framing fix, not a hiding one
    expect(screen.getByText(/intake #7/)).toBeTruthy();
    expect(screen.getByText(/Nobody assigned these to you/)).toBeTruthy();
  });

  it("places the field-guide hint before activity and names Capture directly", async () => {
    render(<MyDay />);
    const hint = await screen.findByText(/Something you have not tried yet/);
    const activity = screen.getByText("Since yesterday");
    expect(hint.compareDocumentPosition(activity) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByText(/Select Capture in the top bar/)).toBeTruthy();
    expect(document.body.textContent).not.toContain("Ctrl+K");
  });
});
