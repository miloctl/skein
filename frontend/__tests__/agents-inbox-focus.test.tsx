import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const trust = {
  last: "",
};

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string) => {
      if (path === "/api/agents")
        return Promise.resolve([
          {
            agent: "agent",
            identity_owner: "core",
            delegatable: true,
            open_tasks: 1,
            pending_proposals: 0,
            last_seen: null,
            authority: [],
          },
        ]);
      if (path === "/api/agents/agent/inbox")
        return Promise.resolve({
          agent: "agent",
          delegated_tasks: [
            {
              id: 25,
              title: "Review the findings",
              description: "Read the study and summarize the evidence.",
              status: "todo",
              priority: "high",
              sponsor: "marcus",
              due_date: "2026-08-31",
              milestone_id: 2,
              engagement_id: 3,
            },
          ],
          open_questions: [],
          rejected_proposals: [],
          notifications: [],
        });
      if (path === "/api/agents/trust")
        return Promise.resolve([
          {
            agent: "agent",
            entity: "task",
            proposed: 1,
            approved: 1,
            rejected: 0,
            approval_rate: 1,
            recent_streak: 0,
            last_verified_verdict: trust.last,
            current_level: "review",
            suggestion: "",
          },
        ]);
      if (path === "/api/agents/entities")
        return Promise.resolve({ entities: [], always_review: [] });
      if (path === "/api/agents/status")
        return Promise.resolve({
          provider: "mock",
          model: "",
          provider_error: "",
          review_gate: true,
          trust_blocked: "",
          runner_agents: [],
          runner_daily_tokens: 0,
          context_strategy: "",
          context_error: "",
        });
      return Promise.resolve([]);
    },
  };
});
vi.mock("next/navigation", () => ({ usePathname: () => "/agents" }));

import AgentsPage from "@/app/agents/page";

describe("Mission control inbox", () => {
  it("opens beside Mission control, moves focus, and shows task context", async () => {
    render(<AgentsPage />);
    const button = await screen.findByRole("button", { name: "inbox" });
    fireEvent.click(button);

    const heading = await screen.findByRole("heading", { name: "Inbox — agent" });
    await waitFor(() => expect(document.activeElement).toBe(heading));
    expect(button.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText(/Read the study and summarize the evidence/)).toBeTruthy();
    expect(screen.getByText(/Engagement #3/)).toBeTruthy();
  });

  it("does not call an absent verified verdict a rejection", async () => {
    trust.last = "";
    render(<AgentsPage />);
    await screen.findByText(/1\/1 approved/);
    expect(document.body.textContent).not.toContain("last verdict was not an approval");
    expect(document.body.textContent).toContain("no verified verdicts");
  });
});
