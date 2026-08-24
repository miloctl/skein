import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/** Machine payloads must not reach a reader. Three surfaces shipped one:
 *  the Approvals diff rendered a task list as `[2]`, the health card
 *  promised "each rating shows why" and showed nothing for a green
 *  engagement, and the slip forecast printed "likely <date>" off zero
 *  completed milestones — no information dressed as a prediction. */

// Every endpoint the pages read needs its real SHAPE: returning [] for the
// forecast makes `forecast.basis` throw, and the page renders nothing at all.
//
// HAND-AUTHORED, and the one place in this suite that is. It has to be: these
// are the payloads of an EMPTY deployment, which is exactly the state a
// running instance with seed data cannot produce, and the defects this file
// pins are all "a page made a claim with no data behind it". The cost is
// drift — `/api/portfolio/flow` silently fell three keys behind
// services/portfolio.py::flow_metrics before anyone noticed, because the page
// reads them optionally. When a key is added there, add it here; the test
// stays green either way, so nothing else will tell you.
const BASE: Record<string, unknown> = {
  "/api/portfolio/flow": {
    cycle_time: { tasks_done: 0, avg_days: null, median_days: null },
    throughput_by_week: {},
    wip_by_person: [],
    wip_total: 0,
    wip_people: 0,
    stale_wip: [],
    interrupts: {
      planned: 0,
      unplanned: 0,
      same_week_unplanned_share: null,
      n: 0,
      carried_over: 0,
      window_weeks: 8,
    },
  },
  "/api/week": { week: "2026-W32", committed: 0, done: 0, kept_percent: null, tasks: [] },
  "/api/portfolio/forecast": {
    basis: { milestones_measured: 0, median_slip_days: 0 },
    forecasts: [],
  },
};
let data: Record<string, unknown> = { ...BASE };
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string) => Promise.resolve(path in data ? data[path] : []),
    getUser: () => "tester",
    subscribeUser: () => () => {},
  };
});
vi.mock("next/navigation", () => ({ usePathname: () => "/review" }));

import ReviewPage from "@/app/review/page";
import PortfolioPage from "@/app/portfolio/page";

beforeEach(() => {
  data = { ...BASE };
});

describe("the Approvals diff table", () => {
  it("renders a list of ids as ids, not as a JSON array", async () => {
    data["/api/review?status=pending&limit=50"] = [
      {
        id: 3,
        entity: "weekly_plan",
        entity_id: null,
        action: "create",
        payload: { week: "2026-W32", task_ids: [2, 7] },
        summary: "the weekly line",
        label: "commit tasks to a week",
        proposed_by: "scheduler",
        requested_by: null,
        origin: "agent",
        created_at: "2026-08-05T00:00:00+00:00",
      },
    ];
    render(<ReviewPage />);
    await screen.findByText("the weekly line");
    const ids = screen.getByText("2, 7");
    expect((ids.closest("details") as HTMLDetailsElement).open).toBe(false);

    fireEvent.click(screen.getByText("Technical details"));

    expect((ids.closest("details") as HTMLDetailsElement).open).toBe(true);
    expect(screen.queryByText("[2,7]")).toBeNull();
  });
});

describe("the Health page and its evidence", () => {
  it("says why a green engagement is green", async () => {
    data["/api/portfolio/health"] = [
      { id: 1, name: "Onboarding revamp", status: "active", lead: "ava", health: "green", receipts: [] },
    ];
    render(<PortfolioPage />);
    // the card title promises a why for EVERY rating
    expect(await screen.findByText(/Nothing flagged/)).toBeTruthy();
  });

  it("does not call a due date 'likely' with nothing measured", async () => {
    data["/api/portfolio/forecast"] = {
      basis: { milestones_measured: 0, median_slip_days: 0 },
      forecasts: [
        {
          milestone_id: 1,
          title: "Scope & success criteria",
          project: "p",
          due_date: "2026-08-07",
          forecast_date: "2026-08-07",
          at_risk: false,
        },
      ],
    };
    render(<PortfolioPage />);
    expect(await screen.findByText(/No milestone has been completed yet/)).toBeTruthy();
    expect(screen.queryByText(/likely/)).toBeNull();
  });
});
