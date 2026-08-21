import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

/** Two word-for-word identical pending proposals sat adjacent in Approvals
 *  with nothing saying so (agents re-file), and a reviewer read both. The
 *  later one carries a badge naming the first. */

const base = {
  entity: "extension_tool",
  entity_id: null,
  action: "create",
  payload: { tool: "atlas.workplace.sync-tool", version: "1.0.0" },
  summary: "Run governed tool atlas.workplace.sync-tool",
  proposed_by: "atlas.workplace.delivery-specialist",
  requested_by: null,
  origin: "agent",
  created_at: "2026-08-15T09:00:00+00:00",
  label: "run a governed extension tool",
};

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string) =>
      path.startsWith("/api/review?status=pending")
        ? Promise.resolve([
            { ...base, id: 4 },
            { ...base, id: 5 },
            {
              ...base,
              id: 13,
              summary: "a different write entirely",
              payload: { other: true },
            },
          ])
        : Promise.resolve([]),
  };
});

vi.mock("next/navigation", () => ({ usePathname: () => "/review" }));

import ReviewPage from "@/app/review/page";

describe("identical pending proposals", () => {
  it("badges the later copy with the first one's number", async () => {
    render(<ReviewPage />);
    expect(await screen.findByText("identical to #4")).toBeTruthy();
    // one badge: the first copy and the differing proposal carry none
    expect(screen.getAllByText(/identical to/)).toHaveLength(1);
  });
});
