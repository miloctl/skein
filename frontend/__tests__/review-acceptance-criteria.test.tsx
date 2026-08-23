import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

/** The sponsor's own definition of done, written at delegation, shown at the
 *  verdict — which is the read it was written for. Absent when the
 *  delegation carried none: an empty "What done means:" line would claim a
 *  contract nobody wrote. */

const row = {
  id: 7,
  entity: "task_completion",
  entity_id: 4,
  action: "update",
  payload: {},
  summary: "mark task #4 done",
  proposed_by: "scout",
  requested_by: null,
  origin: "agent",
  created_at: "2026-08-20T09:00:00+00:00",
  label: "accept completed work",
  evidence: {
    id: 4,
    title: "Build the happy path",
    status: "in_progress",
    delegated_agent: "scout",
    acceptance_criteria: "a runnable repro script, blocker #3 resolved",
    forge_url: null,
    worklog: [],
    sponsor_was: "",
    criteria_refs: [
      { entity: "blocker", id: 3, state: "resolved" },
      { entity: "task", id: 9999, state: "" },
    ],
  },
};

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    getUser: () => "tester",
    subscribeUser: () => () => {},
    api: (path: string) =>
      path.startsWith("/api/review?status=pending")
        ? Promise.resolve([row])
        : Promise.resolve([]),
  };
});
vi.mock("next/navigation", () => ({ usePathname: () => "/review" }));

import ReviewPage from "@/app/review/page";

describe("acceptance criteria at the verdict", () => {
  it("shows what done means beside the evidence", async () => {
    render(<ReviewPage />);
    expect(await screen.findByText("What done means:")).toBeTruthy();
    expect(
      screen.getByText("a runnable repro script, blocker #3 resolved"),
    ).toBeTruthy();
  });

  it("shows each named row's current state, and one sentence for an absent row", async () => {
    render(<ReviewPage />);
    await screen.findByText("What done means:");
    expect(screen.getByText("resolved").parentElement?.textContent).toContain(
      "blocker #3: resolved",
    );
    // absent and hidden rows read alike — the chip must not confirm existence
    expect(screen.getByText("not found").parentElement?.textContent).toContain(
      "task #9999: not found",
    );
  });
});
