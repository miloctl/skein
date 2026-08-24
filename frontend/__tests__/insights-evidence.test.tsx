import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

/** A finding's evidence, readable. The panel rendered JSON.stringify into a
 *  <pre>, so checking a review_stall claim meant reading raw dicts — the ids
 *  and numbers are the receipt, and they must read as sentences. */

const FINDING = {
  id: 7,
  rule_id: "review_stall",
  label: "Review queue",
  audience: "team",
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

const SYSTEM_FINDING = {
  ...FINDING,
  id: 8,
  rule_id: "job_stale",
  label: "Scheduled job",
  audience: "system",
  message: "The notification job is stale.",
};

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string) =>
      path === "/api/insights"
        ? Promise.resolve({
            findings: [FINDING, SYSTEM_FINDING],
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
            rule_stats: [
              {
                rule_id: "review_stall",
                label: "Review queue",
                audience: "team",
                fired: 3,
                dispositioned: 1,
                converted: 1,
                dismissed: 0,
                median_days_to_disposition: 2,
              },
              {
                rule_id: "job_stale",
                label: "Scheduled job",
                audience: "system",
                fired: 4,
                dispositioned: 2,
                converted: 0,
                dismissed: 2,
                median_days_to_disposition: 1,
              },
            ],
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

afterEach(() => window.localStorage.removeItem("skein-manage"));

describe("a finding's evidence panel", () => {
  it("separates team signals from system health", async () => {
    render(<InsightsPage />);
    expect(await screen.findByText(FINDING.message)).toBeTruthy();
    expect(
      screen.getByRole("button", {
        name: /^high: The review queue is stalled/,
      }),
    ).toBeTruthy();
    expect(screen.queryByText(SYSTEM_FINDING.message)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "System health" }));

    expect(screen.getByText(SYSTEM_FINDING.message)).toBeTruthy();
    expect(screen.queryByText(FINDING.message)).toBeNull();
  });

  it("keeps rule follow-through in the selected audience", async () => {
    window.localStorage.setItem("skein-manage", "1");
    render(<InsightsPage />);
    const team = await screen.findByText("Review queue");
    expect(team.closest("li")?.textContent).toContain("fired 3");
    expect(screen.queryByText("Scheduled job")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "System health" }));

    const system = screen.getByText("Scheduled job");
    expect(system.closest("li")?.textContent).toContain("fired 4");
    expect(screen.queryByText("Review queue")).toBeNull();
  });

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
