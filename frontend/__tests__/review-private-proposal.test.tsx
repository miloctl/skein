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
    expect(notes).toHaveLength(1);
    expect(notes[0].closest("li, article, section")?.textContent).toContain("from my chat");
  });
});
