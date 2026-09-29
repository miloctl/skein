import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

/** The posture note's exit trigger calls the season-end decision "a read,
 *  not a debate" (docs/ROADMAP.md). This card is that read, and its most
 *  important row is the zero: settled verdicts with none carrying strong
 *  identity is exactly the condition that narrows the agent surface. */

const state = vi.hoisted(() => ({ readout: null as unknown, trust: null as unknown }));

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
        ? Promise.resolve(state.readout ?? READOUT)
        : path === "/api/agents/trust" && state.trust
          ? Promise.resolve(state.trust)
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

afterEach(() => {
  window.localStorage.removeItem("skein-manage");
  state.readout = null;
  state.trust = null;
});

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

  it("counts routine work on its own line, only when there is some", async () => {
    window.localStorage.setItem("skein-manage", "1");
    state.readout = {
      ...READOUT,
      by_agent: [{ ...READOUT.by_agent[0], routine: 12 }],
      routine: {
        verdicts: { settled: 12, approved: 11, rejected: 1 },
        proposals: 12,
        delegations: { started: 12, accepted: 11 },
      },
    };
    const { unmount } = render(<AgentsPage />);
    expect(
      await screen.findByText(
        /From routines, counted separately: 12 acceptance verdicts \(11 approved · 1 rejected\) · 12 delegations started · 11 accepted\./,
      ),
    ).toBeTruthy();
    expect(screen.getByText(/0 pending · 12 from routines/)).toBeTruthy();
    // the red zero reads the hand counts, which the server sends apart
    expect(screen.getByText(/0 with strong identity/).className).toContain("text-danger");
    unmount();
    state.readout = {
      ...READOUT,
      routine: {
        verdicts: { settled: 0, approved: 0, rejected: 0 },
        proposals: 0,
        delegations: { started: 0, accepted: 0 },
      },
    };
    render(<AgentsPage />);
    await screen.findByText("Season readout — the trust loop");
    await screen.findByText(/0 with strong identity/);
    expect(screen.queryByText(/From routines/)).toBeNull();
  });

  it("names a trust row's routine verdicts apart from its hand verdicts", async () => {
    state.trust = [
      {
        agent: "scout",
        entity: "task_completion",
        proposed: 0,
        approved: 0,
        rejected: 0,
        approval_rate: 0,
        routine_approved: 11,
        routine_rejected: 1,
        recent_streak: 0,
        last_verified_verdict: "",
        current_level: "review",
        suggestion: "",
      },
    ];
    render(<AgentsPage />);
    expect(await screen.findByText(/no verdicts outside routines/)).toBeTruthy();
    expect(screen.getByText(/routines, counted separately: 11 approved, 1 rejected/)).toBeTruthy();
    expect(screen.queryByText(/0\/0 approved/)).toBeNull();
    // strong routine verdicts exist, so the row must not deny them
    expect(screen.queryByText(/no verified verdicts/)).toBeNull();
  });
});
