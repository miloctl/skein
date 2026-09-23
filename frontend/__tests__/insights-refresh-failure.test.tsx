import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/** One failed read must not stand in for the whole page. A reload after a
 *  write that fails keeps the last good page under a banner, a first load
 *  that fails offers a retry, and a read that works clears the failure. */

const mocks = vi.hoisted(() => ({ api: vi.fn(), insightsFail: false }));

// the fixture in insights-finding-conversion.test.tsx
const insights = {
  mttr: {
    window_days: 30,
    current: { n: 0, median_hours: null, p85_hours: null },
    previous: { n: 0, median_hours: null, p85_hours: null },
  },
  automation_ratio: [],
  review_trend: [],
  intake_funnel: {
    window_weeks: 4,
    submitted: 0,
    accepted: 0,
    deferred: 0,
    declined: 0,
    median_days_to_disposition: null,
    dispositioned_n: 0,
  },
  forecast_calibration: {
    n: 0,
    window_days: 90,
    median_error_days: null,
    median_abs_error_days: null,
    hit_rate: null,
    hits: null,
  },
  token_spend_weekly: [],
  adoption: {
    weekly_active_users: 0,
    team_humans: 0,
    non_web_share: null,
    by_surface: [],
  },
  findings: [
    {
      id: 7,
      rule_id: "aging_wip",
      severity: "high",
      message: "Work has aged",
      n: 1,
      window: "week",
      week: "2026-W33",
      receipt: {},
      disposition: "",
    },
  ],
  rule_stats: [],
  pulse_tally: [],
};

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: mocks.api };
});
vi.mock("next/navigation", () => ({ usePathname: () => "/insights" }));

import InsightsPage from "@/app/insights/page";

beforeEach(() => {
  mocks.insightsFail = false;
  mocks.api.mockReset();
  mocks.api.mockImplementation((path: string) =>
    path === "/api/insights"
      ? mocks.insightsFail
        ? Promise.reject(new Error("Cannot reach the backend."))
        : Promise.resolve(insights)
      : Promise.resolve({}),
  );
});

describe("an Insights read that fails", () => {
  it("keeps the last good page when the reload after a write fails", async () => {
    render(<InsightsPage />);
    fireEvent.click(await screen.findByRole("button", { name: /Work has aged/ }));
    mocks.insightsFail = true;
    fireEvent.click(screen.getByRole("button", { name: "resolved" }));
    expect(await screen.findByText(/Last refresh failed/)).toBeTruthy();
    expect(screen.getByRole("button", { name: /Work has aged/ })).toBeTruthy();

    mocks.insightsFail = false;
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Try again" })));
    expect(screen.queryByText(/Last refresh failed/)).toBeNull();
  });

  it("offers a retry when the first load fails", async () => {
    mocks.insightsFail = true;
    render(<InsightsPage />);
    const retry = await screen.findByRole("button", { name: "Try again" });
    mocks.insightsFail = false;
    await act(async () => fireEvent.click(retry));
    expect(await screen.findByRole("button", { name: /Work has aged/ })).toBeTruthy();
    expect(screen.queryByText(/Cannot reach the backend/)).toBeNull();
  });
});
