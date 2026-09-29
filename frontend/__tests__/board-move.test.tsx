import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/** Moving a card: one rule for the drop and the Move panel, no optimistic
 *  move, a blocker for Blocked, and Resolve to leave it
 *  (docs/intent/board-view.md D5, D6). */

const card = (id: number, status: string, extra: Record<string, unknown> = {}) => ({
  id,
  title: `Task ${id}`,
  status,
  priority: "medium",
  assignee: "",
  due_date: null,
  completed_at: null,
  forge_url: null,
  visibility: "crew",
  crew_id: 5,
  committed_week: null,
  delegated_agent: null,
  waiting_on_type: null,
  waiting_on_id: null,
  quiet_days: null,
  blockers: [],
  ...extra,
});

const state = vi.hoisted(() => ({
  reads: 0,
  writes: [] as Array<{ path: string; method: string; body: unknown }>,
  board: {} as Record<string, unknown>,
  fail: null as null | Error,
  hold: null as null | Promise<void>,
  failRead: false,
  paths: [] as string[],
  reportStatus: vi.fn(),
}));

vi.mock("@/lib/status", () => ({ reportStatus: state.reportStatus }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string, init?: RequestInit) => {
      if (init?.method) {
        state.writes.push({ path, method: init.method, body: JSON.parse(String(init.body)) });
        if (state.fail) return Promise.reject(state.fail);
        const answer = path.endsWith("/resolve") ? { task_unblocked: true } : { id: 9 };
        return state.hold ? state.hold.then(() => answer) : Promise.resolve(answer);
      }
      state.reads += 1;
      state.paths.push(path);
      if (state.failRead) return Promise.reject(new real.ApiError("the database is busy", 503));
      return Promise.resolve(state.board);
    },
  };
});
vi.mock("next/navigation", () => ({
  usePathname: () => "/board",
  useSearchParams: () => new URLSearchParams(),
}));

import BoardPage from "@/app/board/page";
import { ApiError } from "@/lib/api";

const openMove = async (id: number) => {
  fireEvent.click(await screen.findByRole("button", { name: new RegExp(`^Move task #${id}:`) }));
  return screen.getByRole("group", { name: `Move task #${id}` });
};

beforeEach(() => {
  state.reads = 0;
  state.writes = [];
  state.fail = null;
  state.hold = null;
  state.failRead = false;
  state.paths = [];
  state.reportStatus.mockReset();
  state.board = {
    scope: null,
    limit: 500,
    done_days: 7,
    today: "2026-09-29",
    open: [
      card(1, "todo"),
      card(2, "blocked", { blockers: [{ id: 4, title: "Vendor key" }] }),
      card(3, "in_progress", { delegated_agent: "scout" }),
    ],
    done: [card(6, "done", { completed_at: "2026-09-28T10:00:00+00:00" })],
  };
});

describe("moving a card", () => {
  it("sends only the status and the status the board loaded, then lands focus on the card", async () => {
    render(<BoardPage />);
    const panel = await openMove(1);
    fireEvent.click(within(panel).getByRole("button", { name: "In progress" }));
    await waitFor(() => expect(state.writes).toHaveLength(1));
    expect(state.writes[0]).toEqual({
      path: "/api/tasks/1",
      method: "PATCH",
      body: { status: "in_progress", expected_status: "todo" },
    });
    await waitFor(() => expect(state.reads).toBe(2));
    expect(state.reportStatus).toHaveBeenCalledWith("Task #1 moved to In progress.", "confirmation");
    await waitFor(() => expect(document.activeElement?.id).toBe("board-move-1"));
  });

  it("raises a blocker at the card's tier for Blocked, and sends no PATCH", async () => {
    render(<BoardPage />);
    fireEvent.click(within(await openMove(1)).getByRole("button", { name: "Blocked…" }));
    const title = screen.getByLabelText("What blocks it?");
    expect(document.activeElement).toBe(title);
    fireEvent.change(title, { target: { value: "Waiting on legal" } });
    fireEvent.click(screen.getByRole("button", { name: /Raise blocker/ }));
    await waitFor(() => expect(state.writes).toHaveLength(1));
    expect(state.writes[0]).toMatchObject({
      path: "/api/blockers",
      method: "POST",
      body: { title: "Waiting on legal", task_id: 1, visibility: "crew", crew_id: 5 },
    });
  });

  it("lists the blockers to resolve instead of the columns", async () => {
    render(<BoardPage />);
    const panel = await openMove(2);
    expect(within(panel).queryByRole("button", { name: "To do" })).toBeNull();
    expect(within(panel).getByText(/Resolve its blockers to move it/)).toBeTruthy();
    fireEvent.click(within(panel).getByRole("button", { name: "Resolve #4 Vendor key" }));
    await waitFor(() => expect(state.writes).toHaveLength(1));
    expect(state.writes[0]).toEqual({
      path: "/api/blockers/4/resolve",
      method: "POST",
      body: { resolution: "resolved from the board" },
    });
    await waitFor(() =>
      expect(state.reportStatus).toHaveBeenCalledWith(
        "Blocker #4 is resolved. Task #2 moved to In progress.",
        "confirmation",
      ),
    );
  });

  it("refuses Blocked for a finished task, and names what to do", async () => {
    render(<BoardPage />);
    fireEvent.click(within(await openMove(6)).getByRole("button", { name: "Blocked…" }));
    expect(state.reportStatus).toHaveBeenCalledWith(
      "A finished task cannot move to Blocked. Reopen task #6 first. Then raise a blocker.",
    );
    expect(state.writes).toEqual([]);
    expect(screen.queryByLabelText("What blocks it?")).toBeNull();
  });

  it("reports a stale board's conflict and rereads it", async () => {
    state.fail = new ApiError("Task #1 changed after you loaded the board. It is now done.", 409);
    render(<BoardPage />);
    fireEvent.click(within(await openMove(1)).getByRole("button", { name: "Done" }));
    await waitFor(() =>
      expect(state.reportStatus).toHaveBeenCalledWith(
        "Task #1 changed after you loaded the board. It is now done.",
      ),
    );
    await waitFor(() => expect(state.reads).toBe(2));
  });

  it("offers a delegated card no Move and no drag", async () => {
    render(<BoardPage />);
    const title = await screen.findByText("Task 3");
    const item = title.closest("li")!;
    expect(within(item).queryByRole("button", { name: /^Move/ })).toBeNull();
    expect(item.getAttribute("draggable")).not.toBe("true");
  });

  it("moves a dropped card by the same rule, and ignores a stray payload", async () => {
    render(<BoardPage />);
    await screen.findByText("Task 1");
    const drop = (id: string, name: RegExp) =>
      act(() => {
        fireEvent.drop(screen.getByRole("region", { name }), { dataTransfer: { getData: () => id } });
      });
    drop("999", /^Done/);
    // the delegated card is In progress: Done is a real move for it
    drop("3", /^Done/);
    expect(state.writes).toEqual([]);
    drop("1", /^In progress/);
    await waitFor(() =>
      expect(state.writes).toEqual([
        { path: "/api/tasks/1", method: "PATCH", body: { status: "in_progress", expected_status: "todo" } },
      ]),
    );
  });

  it("rereads the board the reader sees now, not the one a write began on", async () => {
    let release = () => {};
    state.hold = new Promise((resolve) => (release = resolve));
    render(<BoardPage />);
    fireEvent.click(within(await openMove(1)).getByRole("button", { name: "Done" }));
    await waitFor(() => expect(state.writes).toHaveLength(1));
    fireEvent.click(screen.getByRole("checkbox", { name: "Only my tasks" }));
    await waitFor(() => expect(state.paths.at(-1)).toBe("/api/tasks/board?mine=true"));
    await act(async () => release());
    await waitFor(() => expect(state.reads).toBe(3));
    expect(state.paths.at(-1)).toBe("/api/tasks/board?mine=true");
  });

  it("returns focus to Move when the panel closes by Escape or a refusal", async () => {
    render(<BoardPage />);
    const panel = await openMove(1);
    fireEvent.keyDown(within(panel).getByRole("button", { name: "In progress" }), { key: "Escape" });
    await waitFor(() => expect(document.activeElement?.id).toBe("board-move-1"));
    fireEvent.click(within(await openMove(6)).getByRole("button", { name: "Blocked…" }));
    expect(document.activeElement?.id).toBe("board-move-6");
  });

  it("keeps an open blocker draft on one card when another card moves", async () => {
    render(<BoardPage />);
    fireEvent.click(within(await openMove(1)).getByRole("button", { name: "Blocked…" }));
    fireEvent.change(screen.getByLabelText("What blocks it?"), { target: { value: "half a reason" } });
    act(() => {
      fireEvent.drop(screen.getByRole("region", { name: /^To do/ }), { dataTransfer: { getData: () => "6" } });
    });
    await waitFor(() => expect(state.writes).toHaveLength(1));
    expect((screen.getByLabelText("What blocks it?") as HTMLInputElement).value).toBe("half a reason");
  });

  it("says so when a second move starts before the first one ends", async () => {
    state.hold = new Promise(() => {});
    render(<BoardPage />);
    fireEvent.click(within(await openMove(1)).getByRole("button", { name: "Done" }));
    await waitFor(() => expect(state.writes).toHaveLength(1));
    act(() => {
      fireEvent.drop(screen.getByRole("region", { name: /^To do/ }), { dataTransfer: { getData: () => "6" } });
    });
    expect(state.writes).toHaveLength(1);
    expect(state.reportStatus).toHaveBeenCalledWith("Wait for the move of task #1 to finish. Then try again.");
  });

  it("does not pull focus to a card whose reload failed, on a later reload", async () => {
    render(<BoardPage />);
    state.failRead = true;
    fireEvent.click(within(await openMove(1)).getByRole("button", { name: "Done" }));
    await waitFor(() => expect(state.reads).toBe(2));
    await waitFor(() => expect(screen.getByText(/Could not load this page/)).toBeTruthy());
    state.failRead = false;
    // a new answer object, as a real reread is: the same object is no change
    state.board = { ...state.board };
    const field = screen.getByRole("checkbox", { name: "Only my tasks" });
    field.focus();
    act(() => window.dispatchEvent(new Event("skein-attention-change")));
    await waitFor(() => expect(state.reads).toBe(3));
    await waitFor(() => expect(screen.queryByText(/Could not load this page/)).toBeNull());
    expect(document.activeElement).toBe(field);
  });

  it("lets the Move button close its own open panel", async () => {
    render(<BoardPage />);
    await openMove(1);
    const button = screen.getByRole("button", { name: /^Move task #1:/ });
    expect(fireEvent.mouseDown(button)).toBe(false);
  });

  it("promises the move to In progress only for a Blocked card", async () => {
    state.board = {
      ...state.board,
      done: [card(6, "done", { blockers: [{ id: 8, title: "Late audit" }] })],
    };
    render(<BoardPage />);
    const panel = await openMove(6);
    expect(within(panel).getByText("Resolve its blockers to move it.")).toBeTruthy();
    expect(within(panel).queryByText(/moves to In progress/)).toBeNull();
  });
});
