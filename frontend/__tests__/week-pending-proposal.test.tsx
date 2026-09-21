import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ api: vi.fn(), report: vi.fn() }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: mocks.api };
});
vi.mock("@/lib/status", () => ({ reportStatus: mocks.report, dismissStatus: vi.fn() }));
vi.mock("next/navigation", () => ({ usePathname: () => "/planning" }));
vi.mock("@/components/task-peek", () => ({ PeekLink: ({ children }: { children: React.ReactNode }) => <a>{children}</a> }));

import PlanningPage from "@/app/planning/page";

// GET /api/planning and /api/week/draft on the seeded mock instance, 2026-09-20.
const cockpit = {
  last_week: { week: "2026-W37", committed: 0, done: 0, kept_percent: null, carryover: [] },
  week: { week: "2026-W38", committed: 0, done: 0, kept_percent: null, tasks: [], pending_proposal: null as { id: number; summary: string } | null },
  interrupts: { planned: 0, unplanned: 1, same_week_unplanned_share: null, n: 1, carried_over: 0, window_weeks: 8 },
  capacity_ahead: [], conflicts: [], intake: [], stale_decisions: [], stakeholders: [], awaiting: [], health: [], health_changes: [], top_unblocking_move: null, today: "2026-09-20",
};
const draft = { week: "2026-W38", items: [{ task_id: 2, title: "Write success criteria and out-of-scope list", assignee: "marcus" }], skipped_absent: [] };

function mockWeek(pending: typeof cockpit.week.pending_proposal = null) {
  mocks.api.mockImplementation((path: string) => {
    if (path === "/api/planning") return Promise.resolve({ ...cockpit, week: { ...cockpit.week, pending_proposal: pending } });
    if (path === "/api/week/draft") return Promise.resolve(draft);
    if (path === "/api/week/plan") return Promise.resolve({ week: draft.week, committed: 1, task_ids: [2], skipped: [] });
    return Promise.resolve([]);
  });
}

beforeEach(() => { vi.clearAllMocks(); window.history.replaceState(null, "", "/planning"); });

describe("Planning's weekly plan", () => {
  it("offers the pending proposal instead of a second drafter", async () => {
    mockWeek({ id: 13, summary: "Weekly commitment line 2026-W38: 1 task" });
    render(<PlanningPage />);
    expect((await screen.findByRole("link", { name: "Proposal #13" })).getAttribute("href")).toBe("/review?id=13");
    expect(screen.queryByRole("button", { name: "Draft a plan" })).toBeNull();
    expect(mocks.api).not.toHaveBeenCalledWith("/api/week");
  });

  it("drafts, commits once, clears the draft, and refreshes both projections", async () => {
    mockWeek();
    render(<PlanningPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Draft a plan" }));
    const commit = await screen.findByRole("button", { name: "Add 1 task to the plan" });
    act(() => { commit.click(); commit.click(); });
    await waitFor(() => expect(screen.queryByRole("button", { name: /Add 1 task/ })).toBeNull());
    expect(mocks.api.mock.calls.filter(([p]) => p === "/api/week/plan")).toHaveLength(1);
    expect(mocks.api).toHaveBeenCalledWith("/api/week/plan", { method: "POST", body: JSON.stringify({ week: draft.week, task_ids: [2] }) });
    expect(mocks.api.mock.calls.filter(([p]) => p === "/api/planning")).toHaveLength(2);
    expect(mocks.api.mock.calls.filter(([p]) => p === "/api/interventions?limit=12")).toHaveLength(2);
  });

  it("starts only one draft request and keeps an existing draft if redrafting fails", async () => {
    mockWeek();
    render(<PlanningPage />);
    const button = await screen.findByRole("button", { name: "Draft a plan" });
    act(() => { button.click(); button.click(); });
    await screen.findByRole("button", { name: "Add 1 task to the plan" });
    expect(mocks.api.mock.calls.filter(([p]) => p === "/api/week/draft")).toHaveLength(1);
    mocks.api.mockRejectedValueOnce(new Error("Cannot draft the plan"));
    fireEvent.click(button);
    await waitFor(() => expect(mocks.report).toHaveBeenCalledWith(expect.stringContaining("Cannot draft the plan")));
    expect(screen.getByRole("button", { name: "Add 1 task to the plan" })).toBeTruthy();
  });

  it("keeps a failed commit's draft for retry", async () => {
    mockWeek();
    render(<PlanningPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Draft a plan" }));
    const commit = await screen.findByRole("button", { name: "Add 1 task to the plan" });
    mocks.api.mockRejectedValueOnce(new Error("Cannot save the plan"));
    fireEvent.click(commit);
    await waitFor(() => expect(mocks.report).toHaveBeenCalledWith(expect.stringContaining("Cannot save the plan")));
    expect(screen.getByRole("button", { name: "Add 1 task to the plan" })).toBeTruthy();
    expect(screen.getByText(/Write success criteria/)).toBeTruthy();
  });

  it("reports skipped task IDs without claiming that all drafted tasks were committed", async () => {
    mockWeek();
    render(<PlanningPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Draft a plan" }));
    const commit = await screen.findByRole("button", { name: "Add 1 task to the plan" });
    mocks.api.mockResolvedValueOnce({ week: draft.week, committed: 0, task_ids: [], skipped: [2] });
    fireEvent.click(commit);
    await waitFor(() => expect(mocks.report).toHaveBeenCalledWith("0 tasks added to the plan. Skipped task #2.", "confirmation"));
  });

  it("keeps supporting context behind a focusable agenda summary", async () => {
    mockWeek();
    render(<PlanningPage />);
    await screen.findByRole("button", { name: "Draft a plan" });
    const summary = document.getElementById("planning-weeks-ahead")!;
    expect(summary.tagName).toBe("SUMMARY");
    expect(summary.closest("details")?.open).toBe(false);
    fireEvent.click(summary);
    expect(screen.getByRole("table", { name: /Allocation per person/ })).toBeTruthy();
  });

  it("groups only exact low adoption findings and puts receipts behind labelled disclosures", async () => {
    mockWeek();
    // Captured GET /api/interventions?limit=12 on the seeded mock instance.
    const [row, second] = [
      {
        "kind": "finding_low",
        "entity": "finding",
        "entity_id": 7,
        "title": "'Chat & the bench' has zero team-wide first-uses 30+ days after entering the field guide (2026-07-31) — broken entry point, or a feature nobody wants? The how-to card is on /guide.",
        "condition": "low · Feature unadopted",
        "owner": "",
        "action": "Broken entry point, or a feature nobody wants? The how-to card is on /guide",
        "receipts": [
          {
            "message": "knot: chat, link: /chat, since: 2026-07-31",
            "refs": []
          }
        ],
        "engagement_id": null,
        "link": "/insights"
      },
      {
        "kind": "finding_low",
        "entity": "finding",
        "entity_id": 8,
        "title": "'Page help' has zero team-wide first-uses 30+ days after entering the field guide (2026-08-16) — broken entry point, or a feature nobody wants? The how-to card is on /guide.",
        "condition": "low · Feature unadopted",
        "owner": "",
        "action": "Broken entry point, or a feature nobody wants? The how-to card is on /guide",
        "receipts": [
          {
            "message": "knot: page_help, link: /, since: 2026-08-16",
            "refs": []
          }
        ],
        "engagement_id": null,
        "link": "/insights"
      }
    ];
    const original = mocks.api.getMockImplementation()!;
    mocks.api.mockImplementation((path: string) => path.startsWith("/api/interventions") ? Promise.resolve([row, { ...row, entity_id: 19, title: "Unknown finding", condition: "low · Unknown condition" }, second]) : original(path));
    render(<PlanningPage />);
    await screen.findByRole("button", { name: "Draft a plan" });
    const group = screen.getByText("Feature adoption (2 loaded findings)").closest("details")!;
    expect(group.open).toBe(false);
    expect(screen.getByRole("link", { name: "Unknown finding" })).toBeTruthy();
    expect(screen.getByRole("link", { name: row.title }).closest("details")).toBe(group);
    expect(screen.getByRole("link", { name: "Unknown finding" }).closest("details")).toBeNull();
    fireEvent.click(within(group).getByText("Feature adoption (2 loaded findings)"));
    expect(within(group).getByRole("link", { name: row.title })).toBeTruthy();
    const receipt = within(group).getByText((_, el) => el?.tagName === "SUMMARY" && el.textContent === `Receipts for ${row.title}`).closest("details")!;
    expect(receipt.open).toBe(false);
    fireEvent.click(receipt.querySelector("summary")!);
    expect(within(receipt).getByText(row.receipts[0].message)).toBeTruthy();
    expect(screen.getByText(/3 loaded rows.*12/)).toBeTruthy();
  });

  it("lands on the loaded weekly controls", async () => {
    window.history.replaceState(null, "", "/planning#planning-this-week");
    mockWeek();
    render(<PlanningPage />);
    await screen.findByRole("button", { name: "Draft a plan" });
    expect(document.activeElement?.id).toBe("planning-this-week");
  });
});
