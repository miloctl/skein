import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

/** The posture note's exit trigger calls the season-end decision "a read,
 *  not a debate" (docs/ROADMAP.md). This card is that read, and its most
 *  important row is the zero: settled verdicts with none carrying strong
 *  identity is exactly the condition that narrows the agent surface. */

const READOUT = {
  season: "2026·S6",
  days_left: 23,
  verdicts: { settled: 9, approved: 3, rejected: 6, strong: 0 },
  proposals: 12,
  authority_changes: [{ agent: "scout", entity: "task", level: "autonomous" }],
  delegations: { started: 4, accepted: 2 },
  by_agent: [
    { proposed_by: "scout", proposed: 3, approved: 2, rejected: 1, pending: 0 },
  ],
};

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string) =>
      path === "/api/review/season"
        ? Promise.resolve(READOUT)
        : path === "/api/whoami"
          ? Promise.resolve({ strong: false, can_administer: false })
          : // never settles: the other sections stay mid-load, which is
            // enough — every Card renders its title before its data
            new Promise(() => {}),
    getUser: () => "tester",
  };
});
vi.mock("next/navigation", () => ({ usePathname: () => "/agents" }));

import AgentsPage from "@/app/agents/page";

afterEach(() => window.localStorage.removeItem("skein-manage"));

describe("the season readout card", () => {
  it("renders the exit-trigger read behind Management view", async () => {
    window.localStorage.setItem("skein-manage", "1");
    render(<AgentsPage />);
    expect(await screen.findByText("Season readout — the trust loop")).toBeTruthy();
    expect(screen.getByText(/9/)).toBeTruthy();
    // the zero is the trigger — it must be stated, not omitted
    expect(screen.getByText(/0 with strong identity/)).toBeTruthy();
    expect(screen.getByText(/4 delegations started · 2 accepted/)).toBeTruthy();
    expect(screen.getByText(/scout/)).toBeTruthy();
  });

  it("stays out of the way when Management view are off", async () => {
    render(<AgentsPage />);
    expect(await screen.findByText(/Trust — earned from review verdicts/)).toBeTruthy();
    expect(screen.queryByText("Season readout — the trust loop")).toBeNull();
  });
});
