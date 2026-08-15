import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string) => {
      if (path === "/api/agents")
        return Promise.resolve([
          {
            agent: "worker",
            identity_owner: "generic-agent",
            delegatable: true,
            open_tasks: 1,
            pending_proposals: 0,
            last_seen: null,
            authority: [],
          },
          {
            agent: "reviewer",
            identity_owner: "generic-agent",
            delegatable: true,
            open_tasks: 0,
            pending_proposals: 2,
            last_seen: null,
            authority: [],
          },
          {
            agent: "idle-agent",
            identity_owner: "generic-agent",
            delegatable: true,
            open_tasks: 0,
            pending_proposals: 0,
            last_seen: null,
            authority: [],
          },
        ]);
      if (path === "/api/whoami")
        return Promise.resolve({
          strong: false,
          admin: false,
          can_administer: false,
        });
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
      if (path === "/api/agents/entities")
        return Promise.resolve({ entities: [], always_review: [] });
      return Promise.resolve([]);
    },
  };
});
vi.mock("next/navigation", () => ({ usePathname: () => "/agents" }));

import AgentsPage from "@/app/agents/page";

describe("Mission control work filter", () => {
  it("starts with agents that have work and keeps the full list available", async () => {
    render(<AgentsPage />);
    const mission = await screen.findByRole("region", { name: "Mission control" });

    expect(within(mission).getByText(/worker/)).toBeTruthy();
    expect(within(mission).getByText(/reviewer/)).toBeTruthy();
    expect(within(mission).queryByText(/idle-agent/)).toBeNull();

    fireEvent.click(within(mission).getByRole("button", { name: "All agents" }));
    expect(await within(mission).findByText(/idle-agent/)).toBeTruthy();

    fireEvent.click(within(mission).getByRole("button", { name: "Has work" }));
    expect(within(mission).queryByText(/idle-agent/)).toBeNull();
  });
});
