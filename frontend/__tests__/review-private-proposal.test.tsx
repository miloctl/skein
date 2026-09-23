import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const base = {
  entity: "task",
  entity_id: null,
  action: "create",
  payload: { title: "Ship it" },
  proposed_by: "scout",
  requested_by: "mira",
  origin: "agent",
  created_at: "2026-09-23T09:00:00+00:00",
  label: "add a task",
};

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    getUser: () => "mira",
    subscribeUser: () => () => {},
    api: (path: string) => {
      if (path.startsWith("/api/review?status=pending"))
        return Promise.resolve([
          { ...base, id: 1, summary: "from my chat", review_visibility: "private" },
          { ...base, id: 2, summary: "from the team", review_visibility: "workspace" },
          {
            ...base,
            id: 3,
            entity: "absence",
            summary: "time away",
            payload: { person: "mira", visibility: "private", dates_shared: true },
            review_visibility: "private",
          },
        ]);
      return Promise.resolve([]);
    },
  };
});
vi.mock("next/navigation", () => ({ usePathname: () => "/review" }));

import ReviewPage from "@/app/review/page";

describe("private proposals", () => {
  it("says which proposals reach their owner alone", async () => {
    render(<ReviewPage />);
    await screen.findByText("from the team");
    const notes = screen.getAllByText(/Only you can see this proposal/);
    expect(notes).toHaveLength(2);
    expect(notes[0].closest("li, article, section")?.textContent).toContain("from my chat");
  });

  it("states who reads the row a create makes before the verdict", async () => {
    render(<ReviewPage />);
    await screen.findByText("from the team");
    // the task payload names no tier: it lands at the workspace default
    expect(screen.getAllByText("everyone on the roster").length).toBeGreaterThan(0);
    expect(
      screen.getByText("Visible to only mira, and the team sees the dates"),
    ).toBeTruthy();
  });
});
