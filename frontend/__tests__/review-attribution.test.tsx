import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const base = {
  entity: "task",
  entity_id: null,
  action: "create",
  payload: { title: "Ship it" },
  summary: "create a task",
  proposed_by: "scout",
  requested_by: null,
  created_at: "2026-08-22T09:00:00+00:00",
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
        return Promise.resolve([{ ...base, id: 1, origin: "agent_verified" }]);
      if (path.startsWith("/api/review?status=approved"))
        return Promise.resolve([
          {
            ...base,
            id: 2,
            origin: "agent",
            reviewed_by: "ava",
            reviewed_override: 0,
          },
          {
            ...base,
            id: 3,
            origin: "agent",
            payload: {},
            summary: "",
            text_cleared_at: "2026-09-23T04:00:00+00:00",
            reviewed_by: "ava",
            reviewed_override: 0,
          },
        ]);
      return Promise.resolve([]);
    },
  };
});
vi.mock("next/navigation", () => ({ usePathname: () => "/review" }));

import ReviewPage from "@/app/review/page";

describe("review attribution", () => {
  it("keeps agent authorship and human authorization as separate claims", async () => {
    render(<ReviewPage />);
    const chip = await screen.findByText("agent · approved");
    expect(chip.textContent).toContain(
      "An agent proposed this and a person approved it.",
    );
    const heading = await screen.findByRole("heading", { name: "Recently approved" });
    const details = heading.closest("details")!;
    expect(details.open).toBe(false);
    fireEvent.click(heading.closest("summary")!);
    expect(details.open).toBe(true);
    expect(details.textContent).toContain("by scout · accepted by ava");
    // services/retention.py cleared this one: the row says why it has no text
    expect(details.textContent).toContain("#3 The text was deleted after 180 days.");
  });
});
