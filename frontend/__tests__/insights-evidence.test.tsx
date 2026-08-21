import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

/** A finding's evidence, readable. The panel rendered JSON.stringify into a
 *  <pre>, so checking a review_stall claim meant reading raw dicts — the ids
 *  and numbers are the receipt, and they must read as sentences. */

const FINDING = {
  id: 7,
  rule_id: "review_stall",
  severity: "high",
  message: "The review queue is stalled: 3 proposals older than 72h.",
  n: 3,
  window: "point-in-time",
  week: "2026-W34",
  receipt: {
    pending: [
      { id: 4, summary: "Run governed tool", proposed_by: "atlas", hours: 128 },
      { id: 5, summary: "Run governed tool", proposed_by: "atlas", hours: 128 },
    ],
    oldest_days: 5.3,
  },
  disposition: "",
};

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string) =>
      path === "/api/insights"
        ? Promise.resolve({
            findings: [FINDING],
            mttr: {
              window_days: 28,
              current: { n: 0, median_hours: null, p85_hours: null },
              previous: { n: 0, median_hours: null, p85_hours: null },
            },
            automation_ratio: [],
            review_trend: [],
            intake_funnel: {
              window_weeks: 12,
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
            rule_follow_through: [],
            adoption: {
              weekly_active_users: 0,
              team_humans: 0,
              non_web_share: null,
              by_surface: [],
            },
            weekly_checkin: null,
            token_spend_weekly: [],
          })
        : Promise.resolve([]),
  };
});

vi.mock("next/navigation", () => ({ usePathname: () => "/insights" }));

import InsightsPage from "@/app/insights/page";

describe("a finding's evidence panel", () => {
  it("renders stored rows as reading lines, never as JSON", async () => {
    render(<InsightsPage />);
    fireEvent.click(await screen.findByText(/review queue is stalled/));
    expect(
      screen.getByText(/#4 · summary: Run governed tool · proposed by: atlas · hours: 128/),
    ).toBeTruthy();
    expect(screen.getByText(/5\.3/)).toBeTruthy();
    const panel = document.getElementById("receipt-7")!;
    expect(panel.textContent).not.toContain("{");
    expect(panel.textContent).not.toContain('"');
  });
});
