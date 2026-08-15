import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ api: vi.fn() }));

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
  window.history.replaceState({}, "", "/insights");
  mocks.api.mockReset();
  mocks.api.mockImplementation((path: string) =>
    path === "/api/insights"
      ? Promise.resolve(insights)
      : Promise.resolve({ id: 42, task_id: 42 }),
  );
});

describe("finding conversion", () => {
  it("opens the task that the conversion created", async () => {
    const opened = vi.fn();
    window.addEventListener("skein-peek", opened);
    render(<InsightsPage />);

    fireEvent.click(await screen.findByRole("button", { name: /Work has aged/ }));
    fireEvent.click(screen.getByRole("button", { name: "→ task" }));

    await waitFor(() => expect(new URL(window.location.href).searchParams.get("task")).toBe("42"));
    expect(opened).toHaveBeenCalled();
    expect(mocks.api).toHaveBeenCalledWith(
      "/api/findings/7/convert",
      expect.objectContaining({ method: "POST" }),
    );
    window.removeEventListener("skein-peek", opened);
  });
});
