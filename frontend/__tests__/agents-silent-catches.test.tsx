import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

/** The Agents page answers one question: what can the agents do without
 *  asking? Six of its fetches used to swallow their failures, and three
 *  then rendered a CLAIM — "No reviewed proposals yet", "Nothing remembered
 *  yet" — while the bench and the status strip simply vanished. On this
 *  page absence reads as "nothing to see", which is the most expensive
 *  wrong answer in the product: it says the agents are idle and unarmed
 *  when the truth is that nobody knows. */

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: () => Promise.reject(new Error("agents service exploded")),
    getUser: () => "tester",
  };
});
vi.mock("next/navigation", () => ({ usePathname: () => "/agents" }));

import AgentsPage from "@/app/agents/page";

describe("the Agents page when every fetch fails", () => {
  it("reports each section instead of claiming it is empty", async () => {
    render(<AgentsPage />);
    const failures = await screen.findAllByText(/Cannot load .*agents service exploded/);
    // agents list, trust, bench, status, memories — each says so itself
    expect(failures.length).toBeGreaterThanOrEqual(4);

    // and none of the false "there is nothing here" claims survive
    expect(screen.queryByText(/No reviewed proposals yet/)).toBeNull();
    expect(screen.queryByText(/Nothing remembered yet/)).toBeNull();
    // the headline list must not sit on "Loading…" after it failed
    expect(screen.queryByText("Loading…")).toBeNull();
  });
});
