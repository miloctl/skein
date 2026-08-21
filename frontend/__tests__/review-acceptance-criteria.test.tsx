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
    acceptance_criteria: "a runnable repro script",
    forge_url: null,
    worklog: [],
    sponsor_was: "",
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
    expect(screen.getByText("a runnable repro script")).toBeTruthy();
  });
});
