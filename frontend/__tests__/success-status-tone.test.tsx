import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** A write that worked is a confirmation. The failure tone puts it in the
 *  assertive region with a red toast and a dismiss button, which tells the
 *  reader that "Task #4 is done." did not happen. */

const task = { id: 4, title: "Build the happy path", status: "todo", priority: "high" };
// GET /api/planning shape, as in week-pending-proposal.test.tsx
const cockpit = {
  last_week: { week: "2026-W37", committed: 0, done: 0, kept_percent: null, carryover: [] },
  week: { week: "2026-W38", committed: 0, done: 0, kept_percent: null, tasks: [], pending_proposal: null },
  interrupts: { planned: 0, unplanned: 1, same_week_unplanned_share: null, n: 1, carried_over: 0, window_weeks: 8 },
  capacity_ahead: [], conflicts: [], intake: [], stale_decisions: [], stakeholders: [], awaiting: [], health: [], health_changes: [], top_unblocking_move: null, today: "2026-09-20",
};
// services/intervention.py, the blocker_escalated row
const escalated = {
  kind: "blocker_escalated",
  entity: "blocker",
  entity_id: 3,
  title: "Vendor API keys",
  condition: "blocker #3 escalated at high impact",
  owner: "dana",
  action: "Unblock dana or take it off them",
  receipts: [],
  link: "/dashboard#blocker-3",
};

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string) => {
      if (path === "/api/agents") return Promise.resolve([{ agent: "scout", delegatable: true }]);
      if (path.includes("/worklog")) return Promise.resolve([]);
      if (path === "/api/planning") return Promise.resolve(cockpit);
      if (path.startsWith("/api/interventions")) return Promise.resolve([escalated]);
      if (path.startsWith("/api/tasks/4")) return Promise.resolve(task);
      return Promise.resolve({});
    },
  };
});
vi.mock("next/navigation", () => ({ usePathname: () => "/planning" }));

import PlanningPage from "@/app/planning/page";
import { TaskPeek } from "@/components/task-peek";
import { dismissStatus, getStatus } from "@/lib/status";

beforeEach(() => window.history.replaceState({}, "", "/"));
afterEach(() => act(() => dismissStatus()));

describe("the tone of a write that worked", () => {
  it("confirms a task marked done", async () => {
    window.history.replaceState({}, "", "/?task=4");
    render(<TaskPeek />);
    fireEvent.click(await screen.findByRole("button", { name: /Mark task #4/ }));
    await waitFor(() => expect(getStatus()?.message).toBe("Task #4 is done."));
    expect(getStatus()?.tone).toBe("confirmation");
  });

  it("confirms a delegation", async () => {
    window.history.replaceState({}, "", "/?task=4");
    render(<TaskPeek />);
    fireEvent.change(await screen.findByLabelText("Delegate to"), { target: { value: "scout" } });
    fireEvent.click(screen.getByRole("button", { name: "Delegate" }));
    await waitFor(() => expect(getStatus()?.message).toMatch(/delegated to scout/));
    expect(getStatus()?.tone).toBe("confirmation");
  });

  it("confirms a queue action on Planning", async () => {
    window.history.replaceState({}, "", "/planning");
    render(<PlanningPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Resolve blocker #3: Vendor API keys" }));
    await waitFor(() => expect(getStatus()?.message).toBe("Blocker #3 resolved."));
    expect(getStatus()?.tone).toBe("confirmation");
  });
});
