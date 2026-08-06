import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

/** The Agents page answers one question: what can the agents do without
 *  asking? Its empty states are CLAIMS — "No reviewed proposals yet",
 *  "Nothing remembered yet", "No rules yet — everything needs approval" —
 *  and on this page a claim rendered while the data is unknown (loading or
 *  failed) is the most expensive wrong answer in the product: it says the
 *  agents are idle and unarmed when the truth is that nobody knows. */

const mode = { fail: true };
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: () =>
      mode.fail
        ? Promise.reject(new Error("agents service exploded"))
        : new Promise(() => {}), // never settles: the page stays mid-load
    getUser: () => "tester",
  };
});
vi.mock("next/navigation", () => ({ usePathname: () => "/agents" }));

import AgentsPage from "@/app/agents/page";

const CLAIMS = [
  /No reviewed proposals yet/,
  /Nothing remembered yet/,
  /No rules yet/,
  /No agent identities yet/,
  /No flock has flown yet/,
];

describe("the Agents page when every fetch fails", () => {
  it("reports each section instead of claiming it is empty", async () => {
    mode.fail = true;
    render(<AgentsPage />);
    // each section says so itself — a count hides a miskeyed error record
    expect(await screen.findAllByText(/Cannot load the agents list/)).toBeTruthy();
    for (const what of [
      /Cannot load trust scores/,
      /Cannot load the bench/,
      /Cannot load the model and review-gate status/,
      /Cannot load team memory/,
      /Cannot load flock traces/,
    ]) {
      expect(screen.getByText(what)).toBeTruthy();
    }

    // and none of the false "there is nothing here" claims survive
    for (const claim of CLAIMS) expect(screen.queryByText(claim)).toBeNull();
    // the page must not sit on "Loading…" after the answer arrived
    expect(screen.queryByText("Loading…")).toBeNull();
  });
});

describe("the Agents page mid-load", () => {
  it("says Loading, and claims nothing", () => {
    mode.fail = false;
    try {
      render(<AgentsPage />);
      expect(screen.getAllByText("Loading…").length).toBeGreaterThan(0);
      // "No rules yet — everything needs approval" mid-load asserts a
      // permissive default nobody has checked; same for trust and memory
      for (const claim of CLAIMS) expect(screen.queryByText(claim)).toBeNull();
    } finally {
      mode.fail = true;
    }
  });
});
