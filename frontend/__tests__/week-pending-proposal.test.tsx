import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/** The weekly-plan card against a plan proposal that already waits in
 *  Approvals. Before this, the card said "Nothing committed this week yet"
 *  and offered "Draft a plan" while the scheduler's proposal for the SAME
 *  week sat pending — and the drafter it invited filed a duplicate. */

const mocks = vi.hoisted(() => ({ api: vi.fn() }));

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: mocks.api };
});
vi.mock("next/navigation", () => ({ usePathname: () => "/portfolio" }));

import PortfolioPage from "@/app/portfolio/page";

function mockWeek(pending: { id: number; summary: string } | null) {
  mocks.api.mockImplementation((path: string) => {
    if (path === "/api/week")
      return Promise.resolve({
        week: "2026-W34",
        committed: 0,
        done: 0,
        kept_percent: null,
        tasks: [],
        pending_proposal: pending,
      });
    if (path === "/api/promises") return Promise.resolve([]);
    if (path === "/api/portfolio/conflicts") return Promise.resolve([]);
    if (path === "/api/usage")
      return Promise.resolve({
        month: { month: "2026-08", calls: 0, cost_usd: null, budget_usd: null, unpriced_calls: 0 },
        engagements: [],
        models: [],
      });
    if (path === "/api/portfolio/flow")
      return Promise.resolve({
        cycle_time: { tasks_done: 0, median_days: null, avg_days: null },
        wip_by_person: [],
        throughput_by_week: [],
        stale_wip: [],
      });
    if (path === "/api/portfolio/forecast")
      return Promise.resolve({
        basis: { milestones_measured: 0, median_slip_days: null },
        forecasts: [],
      });
    return Promise.resolve([]);
  });
}

beforeEach(() => vi.clearAllMocks());

describe("the weekly-plan card", () => {
  it("offers the pending proposal instead of a second drafter", async () => {
    mockWeek({ id: 13, summary: "Weekly commitment line 2026-W34: 3 tasks" });
    render(<PortfolioPage />);
    const link = await screen.findByRole("link", { name: "Proposal #13" });
    expect(link.getAttribute("href")).toBe("/review?id=13");
    expect(screen.queryByRole("button", { name: "Draft a plan" })).toBeNull();
  });

  it("offers the drafter when no plan proposal waits", async () => {
    mockWeek(null);
    render(<PortfolioPage />);
    expect(
      await screen.findByRole("button", { name: "Draft a plan" }),
    ).toBeTruthy();
    expect(screen.queryByText(/already proposes/)).toBeNull();
  });
});
