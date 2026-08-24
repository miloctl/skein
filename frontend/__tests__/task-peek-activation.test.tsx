import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  provider: "ollama",
  providerError: "",
  runnerAgents: [] as string[],
  wakeup: null as null | Record<string, unknown>,
}));

vi.mock("next/navigation", () => ({ usePathname: () => "/dashboard" }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string) => {
      if (path === "/api/agents/status")
        return Promise.resolve({
          provider: state.provider,
          provider_error: state.providerError,
          runner_agents: state.runnerAgents,
        });
      if (path.includes("/worklog")) return Promise.resolve([]);
      return Promise.resolve({
        id: 80,
        title: "review Skein plans",
        status: "todo",
        priority: "medium",
        assignee: "backend-architect",
        delegated_agent: "backend-architect",
        sponsor: "sponsor",
        acceptance_criteria: "",
        check_in_at: "2026-08-23",
        agent_wakeup: state.wakeup ?? undefined,
      });
    },
  };
});

import { TaskPeek } from "@/components/task-peek";

afterEach(() => vi.useRealTimers());

beforeEach(() => {
  state.provider = "ollama";
  state.providerError = "";
  state.runnerAgents = [];
  state.wakeup = null;
  window.history.replaceState({}, "", "/dashboard?task=80");
});

describe("delegated task activation guidance", () => {
  it("shows a durable pending wake without offering a duplicate Chat run", async () => {
    state.wakeup = {
      status: "pending",
      requested_at: "2026-08-24T12:00:00+00:00",
      started_at: "",
      finished_at: "",
      reason: "",
      automation_enabled: true,
    };
    render(<TaskPeek />);

    expect(
      await screen.findByText(
        "backend-architect is queued. Skein will start the agent turn shortly.",
      ),
    ).toBeTruthy();
    expect(screen.queryByRole("link", { name: /Call backend-architect/ })).toBeNull();
  });

  it("refreshes an active wake until the run reaches a terminal state", async () => {
    vi.useFakeTimers();
    state.wakeup = {
      status: "pending",
      requested_at: "2026-08-24T12:00:00+00:00",
      started_at: "",
      finished_at: "",
      reason: "",
      automation_enabled: true,
    };
    render(<TaskPeek />);
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.getByText(/is queued/)).toBeTruthy();

    // an unchanged poll must arm the NEXT timer — a one-shot poll passes only
    // when the very first tick happens to see the status change
    await act(async () => {
      vi.advanceTimersByTime(2000);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByText(/is queued/)).toBeTruthy();

    state.wakeup = {
      ...state.wakeup,
      status: "completed",
      finished_at: "2026-08-24T12:00:02+00:00",
    };
    await act(async () => {
      vi.advanceTimersByTime(2000);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(
      screen.getByText(
        "The agent turn finished. Read the worklog or the acceptance proposal for the result.",
      ),
    ).toBeTruthy();
  });

  it("warns when a prior agent turn has unknown completion", async () => {
    state.wakeup = {
      status: "completion_unknown",
      requested_at: "2026-08-24T12:00:00+00:00",
      started_at: "2026-08-24T12:00:01+00:00",
      finished_at: "2026-08-24T12:05:00+00:00",
      reason: "process_restarted",
      automation_enabled: true,
    };
    render(<TaskPeek />);

    expect(
      await screen.findByText(
        "The agent turn can have written records. Read the worklog and Inbox before you retry.",
      ),
    ).toBeTruthy();
    expect(screen.queryByRole("link", { name: /Call backend-architect/ })).toBeNull();
  });

  it.each([
    ["running", "backend-architect is working its delegated inbox."],
    [
      "completed",
      "The agent turn finished. Read the worklog or the acceptance proposal for the result.",
    ],
    [
      "refused",
      "The agent did not start because its daily token budget is spent.",
      "budget_spent",
    ],
    ["failed", "The agent failed before the model turn started.", "build_failed"],
  ])("shows the durable %s state", async (status, message, reason = "") => {
    state.wakeup = {
      status,
      requested_at: "2026-08-24T12:00:00+00:00",
      started_at: status === "running" ? "2026-08-24T12:00:01+00:00" : "",
      finished_at: status === "running" ? "" : "2026-08-24T12:00:02+00:00",
      reason,
      automation_enabled: true,
    };
    render(<TaskPeek />);
    expect(await screen.findByText(message)).toBeTruthy();
  });

  it("names deterministic mode when a mock deployment refuses the wake", async () => {
    state.provider = "mock";
    state.wakeup = {
      status: "refused",
      requested_at: "2026-08-24T12:00:00+00:00",
      started_at: "",
      finished_at: "2026-08-24T12:00:01+00:00",
      reason: "provider_unavailable",
      automation_enabled: true,
    };
    render(<TaskPeek />);

    // mock is the working keyless default — a fault-shaped "provider is
    // unavailable" sentence on every delegation reads as breakage
    expect(
      await screen.findByText(
        "This workspace uses deterministic mode. Agent model turns are not available.",
      ),
    ).toBeTruthy();
    expect(screen.queryByRole("link", { name: /Call backend-architect/ })).toBeNull();
  });

  it("states that an unlisted agent waits for a Chat activation", async () => {
    render(<TaskPeek />);

    expect(await screen.findByText("This agent will not start automatically.")).toBeTruthy();
    const call = screen.getByRole("link", { name: "Call backend-architect in Chat" });
    const href = new URL(call.getAttribute("href") ?? "", "http://skein.test");
    const compose = href.searchParams.get("compose") ?? "";
    expect(href.pathname).toBe("/chat");
    expect(compose).toContain("/as backend-architect");
    expect(compose).toContain("task #80");
  });

  it("states when the scheduled runner includes the delegated agent", async () => {
    state.runnerAgents = ["backend-architect"];
    render(<TaskPeek />);

    expect(
      await screen.findByText(
        "Scheduled runs include backend-architect. It can claim this task during the next agent run.",
      ),
    ).toBeTruthy();
    expect(screen.getByRole("link", { name: "Call backend-architect in Chat" })).toBeTruthy();
  });

  it("does not offer a Chat activation when the provider cannot run the agent", async () => {
    state.provider = "mock";
    render(<TaskPeek />);

    expect(
      await screen.findByText(
        "This workspace uses deterministic mode. Agent model turns are not available.",
      ),
    ).toBeTruthy();
    expect(screen.queryByRole("link", { name: /Call backend-architect/ })).toBeNull();
  });
});
