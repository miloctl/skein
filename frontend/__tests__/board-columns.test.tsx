import { act, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/** The board page: four status columns from GET /api/tasks/board, a cap
 *  line that makes an empty column claim nothing, and reloads on events
 *  that bypass the 15-second GET cache (docs/intent/board-view.md). */

const card = (id: number, status: string, extra: Record<string, unknown> = {}) => ({
  id,
  title: `Task ${id}`,
  status,
  priority: "medium",
  assignee: "",
  due_date: null,
  completed_at: status === "done" ? "2026-09-28T10:00:00+00:00" : null,
  forge_url: null,
  visibility: "workspace",
  crew_id: null,
  committed_week: null,
  delegated_agent: null,
  waiting_on_type: null,
  waiting_on_id: null,
  quiet_days: null,
  blockers: [],
  ...extra,
});

const state = vi.hoisted(() => ({
  calls: [] as Array<{ path: string; init?: RequestInit }>,
  board: {} as Record<string, unknown>,
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string, init?: RequestInit) => {
      state.calls.push({ path, init });
      return Promise.resolve(state.board);
    },
  };
});
vi.mock("next/navigation", () => ({
  usePathname: () => "/board",
  useSearchParams: () => new URLSearchParams(window.location.search),
}));

import BoardPage from "@/app/board/page";

const column = (name: RegExp) => screen.getByRole("region", { name });

beforeEach(() => {
  state.calls = [];
  window.history.replaceState(null, "", "/board");
  state.board = {
    scope: null,
    limit: 500,
    done_days: 7,
    today: "2026-09-29",
    open: [
      card(1, "todo", { due_date: "2026-09-20", assignee: "ava", committed_week: "2026-W40" }),
      card(2, "in_progress", { quiet_days: 9 }),
      card(3, "blocked", {
        blockers: [
          { id: 4, title: "Vendor key" },
          { id: 5, title: "Legal" },
          { id: 6, title: "Budget" },
        ],
      }),
    ],
    done: [],
  };
});

describe("the board", () => {
  it("puts each card in its status column and counts only what arrived", async () => {
    render(<BoardPage />);
    await screen.findByText("Task 1");
    expect(within(column(/^To do \(1\)/)).getByText("Task 1")).toBeTruthy();
    expect(within(column(/^In progress \(1\)/)).getByText("Not moved for 9 days")).toBeTruthy();
    const blocked = column(/^Blocked \(1\)/);
    expect(within(blocked).getByText("Blocked by #4 Vendor key")).toBeTruthy();
    expect(within(blocked).getByText("+1 more")).toBeTruthy();
    expect(within(blocked).queryByText(/Budget/)).toBeNull();
    expect(within(column(/^To do/)).getByText(/overdue/)).toBeTruthy();
    // Done carries a number, so it states the window plainly
    expect(within(column(/^Done \(0\)/)).getByText("Nothing finished in the last 7 days.")).toBeTruthy();
    expect(screen.queryByText(/This board shows the first/)).toBeNull();
  });

  it("says where it stops at the cap, and an empty open column claims nothing", async () => {
    state.board = { ...state.board, limit: 3 };
    render(<BoardPage />);
    expect(
      await screen.findByText(
        "This board shows the first 3 open tasks, highest priority first. Open one engagement to see the rest.",
      ),
    ).toBeTruthy();
    state.board = { ...state.board, open: [card(1, "todo"), card(2, "todo"), card(7, "todo")] };
    act(() => window.dispatchEvent(new Event("skein-attention-change")));
    await screen.findByText("Task 7");
    expect(screen.queryByText("Nothing is blocked.")).toBeNull();
    expect(screen.queryByText("Nothing in progress.")).toBeNull();
  });

  it("rereads past the GET cache after the task panel closes and on return to the tab", async () => {
    render(<BoardPage />);
    await screen.findByText("Task 1");
    expect(state.calls).toHaveLength(1);
    act(() => window.dispatchEvent(new CustomEvent("skein-peek-close", { detail: { taskId: 1 } })));
    await waitFor(() => expect(state.calls).toHaveLength(2));
    act(() => document.dispatchEvent(new Event("visibilitychange")));
    await waitFor(() => expect(state.calls).toHaveLength(3));
    for (const call of state.calls) expect(call.init?.cache).toBe("no-store");
  });

  it("narrows to the engagement the link names, and offers the way back", async () => {
    window.history.replaceState(null, "", "/board?engagement=3");
    state.board = { ...state.board, scope: { kind: "engagement", id: 3, title: "Atlas" } };
    render(<BoardPage />);
    expect(await screen.findByRole("link", { name: "Atlas" })).toBeTruthy();
    expect(state.calls[0].path).toBe("/api/tasks/board?engagement_id=3");
    expect(screen.getByRole("link", { name: "Show all work" }).getAttribute("href")).toBe("/board");
    screen.getByRole("checkbox", { name: "Only my tasks" }).click();
    await waitFor(() => expect(state.calls.at(-1)?.path).toBe("/api/tasks/board?engagement_id=3&mine=true"));
  });
});
