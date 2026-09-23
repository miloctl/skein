import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/** Two dashboard lines are sentences built from data: the season countdown
 *  must agree with its number, and an activity action must read as words
 *  however many underscores its identifier carries. */

const mocks = vi.hoisted(() => ({ api: vi.fn() }));

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: mocks.api };
});
vi.mock("next/navigation", () => ({ usePathname: () => "/dashboard" }));

import Dashboard from "@/app/dashboard/page";

// the Pulse type in app/dashboard/page.tsx, one day before the season ends
const pulse = {
  season: { label: "S5", days_left: 1 },
  standup_chain: { chain: 2, humans: 3 },
  blocker_speedrun: [],
  season_totals: { engagements_shipped: 0, milestones_shipped: 0, blockers_spotted: 0, blockers_open: 0, lessons_recorded: 0 },
};

beforeEach(() => {
  vi.clearAllMocks();
  mocks.api.mockImplementation((path: string) => {
    if (path === "/api/tasks/browse") return Promise.resolve({ open: [], done: [] });
    if (path === "/api/pulse") return Promise.resolve(pulse);
    // services/api_keys.py logs this action when a key is minted
    if (path === "/api/activity")
      return Promise.resolve([
        { id: 1, actor: "ava", action: "create_api_key", detail: "#3 laptop", created_at: "2026-09-20T09:00:00+00:00" },
      ]);
    return Promise.resolve([]);
  });
});

describe("dashboard sentences", () => {
  it("says 1 day left, not 1 days left", async () => {
    render(<Dashboard />);
    expect(await screen.findByText("1 day left")).toBeTruthy();
    expect(screen.queryByText("1 days left")).toBeNull();
  });

  it("replaces every underscore in an activity action", async () => {
    render(<Dashboard />);
    fireEvent.change(await screen.findByRole("combobox", { name: "Browse register" }), {
      target: { value: "browse-recent-activity" },
    });
    expect(await screen.findByText(/create api key/)).toBeTruthy();
    expect(screen.queryByText(/api_key/)).toBeNull();
  });
});
