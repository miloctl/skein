import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { api } = vi.hoisted(() => ({
  api: vi.fn((path: string) => {
    if (path === "/api/portfolio/health") return Promise.resolve([]);
    if (path === "/api/portfolio/conflicts") return Promise.resolve([]);
    if (path === "/api/portfolio/flow")
      return Promise.resolve({
        cycle_time: { tasks_done: 0, avg_days: null, median_days: null },
        throughput_by_week: {},
        wip_by_person: [],
        stale_wip: [],
      });
    if (path === "/api/week")
      return Promise.resolve({ week: "2026-W33", committed: 0, done: 0, kept_percent: null, tasks: [] });
    if (path === "/api/portfolio/forecast")
      return Promise.resolve({ basis: { milestones_measured: 0, median_slip_days: 0 }, forecasts: [] });
    if (path === "/api/promises") return Promise.resolve([]);
    if (path === "/api/usage")
      return Promise.resolve({
        models: [],
        engagements: [],
        month: { month: "2026-08", cost_usd: null, unpriced_calls: 0, calls: 0, budget_usd: null },
        prices_error: "",
      });
    if (path === "/api/rituals/week-close")
      return Promise.resolve({
        week: "2026-W33-close",
        skipped: "already ran this week",
        artifact_id: 42,
      });
    return Promise.reject(new Error(`unexpected API call: ${path}`));
  }),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api };
});

vi.mock("next/navigation", () => ({ usePathname: () => "/portfolio" }));

import PortfolioPage from "@/app/portfolio/page";

beforeEach(() => {
  api.mockClear();
  window.localStorage.setItem("skein-manage", "1");
});

describe("week ritual receipts", () => {
  it("shows a skipped result and links the existing report", async () => {
    render(<PortfolioPage />);

    fireEvent.click(await screen.findByRole("button", { name: "Run Friday close-out" }));

    expect(await screen.findByText("This ritual already ran this week. Skein did not send duplicate notifications.")).toBeTruthy();
    const link = screen.getByRole("link", {
      name: "Read the existing report on Work → Reports",
    });
    expect(link.getAttribute("href")).toBe("/artifacts?id=42");
  });
});
