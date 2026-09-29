import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/** A blocker is what sets a task to Blocked (services/blockers.py). The task
 *  panel offered a bare "blocked" status instead, so a task could read
 *  Blocked with nothing on record as blocking it, no owner and no escalation
 *  clock. The panel now raises a real blocker against the task. */

const state = vi.hoisted(() => ({
  status: "in_progress",
  posts: [] as Array<{ path: string; body: unknown }>,
  fail: false,
}));

vi.mock("next/navigation", () => ({ usePathname: () => "/dashboard" }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string, init?: RequestInit) => {
      if (init?.method === "POST" && path === "/api/blockers") {
        const body = JSON.parse(String(init.body));
        state.posts.push({ path, body });
        if (state.fail)
          return Promise.reject(new real.ApiError("blocker title is required", 400));
        state.status = "blocked";
        return Promise.resolve({ id: 9, title: body.title, task_id: 80 });
      }
      if (path === "/api/agents/status")
        return Promise.resolve({ provider: "mock", provider_error: "", runner_agents: [] });
      if (path.includes("/worklog")) return Promise.resolve([]);
      if (path.startsWith("/api/tasks/80"))
        return Promise.resolve({
          id: 80,
          title: "migrate the billing tables",
          status: state.status,
          priority: "medium",
          assignee: "mira",
          visibility: "crew",
          crew_id: 3,
          blockers: [],
        });
      return Promise.resolve([]);
    },
  };
});

import { StatusRegion } from "@/components/status-region";
import { TaskPeek } from "@/components/task-peek";

beforeEach(() => {
  state.status = "in_progress";
  state.posts = [];
  state.fail = false;
  window.history.replaceState({}, "", "/dashboard?task=80");
});

const open = async () => {
  render(
    <>
      <TaskPeek />
      <StatusRegion />
    </>,
  );
  fireEvent.click(await screen.findByRole("button", { name: /Block task #80/ }));
};

describe("raise a blocker from the task panel", () => {
  it("files a blocker against the task at the task's own tier", async () => {
    await open();
    fireEvent.change(screen.getByLabelText("What blocks it?"), {
      target: { value: "Waiting for the DBA to grant access" },
    });
    fireEvent.change(screen.getByLabelText("Impact"), { target: { value: "high" } });
    fireEvent.click(screen.getByRole("button", { name: "Raise blocker" }));

    await waitFor(() =>
      expect(screen.getByRole("status").textContent).toContain(
        "Blocker #9 is open on task #80.",
      ),
    );
    expect(state.posts).toEqual([
      {
        path: "/api/blockers",
        body: {
          title: "Waiting for the DBA to grant access",
          impact: "high",
          owner: "",
          task_id: 80,
          visibility: "crew",
          crew_id: 3,
        },
      },
    ]);
  });

  it("hands focus to the edit control once the blocker is filed", async () => {
    await open();
    fireEvent.change(screen.getByLabelText("What blocks it?"), {
      target: { value: "Waiting for the DBA" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Raise blocker" }));
    const edit = await screen.findByRole("button", { name: /Edit task #80/ });
    await waitFor(() => expect(document.activeElement).toBe(edit));
  });

  it.each([["What blocks it?"], ["Impact"]])(
    "Escape in %s closes the form and keeps the panel open",
    async (label) => {
      await open();
      fireEvent.keyDown(screen.getByLabelText(label), { key: "Escape" });
      await waitFor(() =>
        expect(document.activeElement).toBe(
          screen.getByRole("button", { name: /Block task #80/ }),
        ),
      );
      expect(screen.getByRole("dialog")).toBeTruthy();
      expect(window.location.search).toBe("?task=80");
    },
  );

  it("needs a reason before it raises", async () => {
    await open();
    const raise = screen.getByRole("button", { name: "Raise blocker" }) as HTMLButtonElement;
    expect(raise.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("What blocks it?"), { target: { value: "   " } });
    expect(raise.disabled).toBe(true);
  });

  it("keeps the draft and says why when the server refuses", async () => {
    state.fail = true;
    await open();
    const reason = screen.getByLabelText("What blocks it?") as HTMLInputElement;
    fireEvent.change(reason, { target: { value: "vendor outage" } });
    fireEvent.click(screen.getByRole("button", { name: "Raise blocker" }));
    await waitFor(() =>
      expect(document.body.textContent).toContain("blocker title is required"),
    );
    expect(reason.value).toBe("vendor outage");
  });

  it("returns focus to the control that opened it on cancel", async () => {
    await open();
    fireEvent.click(screen.getByRole("button", { name: "cancel" }));
    await waitFor(() =>
      expect(document.activeElement).toBe(
        screen.getByRole("button", { name: /Block task #80/ }),
      ),
    );
  });

  it("no longer offers a bare blocked status in the edit form", async () => {
    render(<TaskPeek />);
    fireEvent.click(await screen.findByRole("button", { name: /Edit task #80/ }));
    const status = screen.getByLabelText("Status") as HTMLSelectElement;
    expect([...status.options].map((o) => o.value)).toEqual(["todo", "in_progress", "done"]);
  });

  it("shows void as the current status of a void task", async () => {
    state.status = "void";
    render(<TaskPeek />);
    fireEvent.click(await screen.findByRole("button", { name: /Edit task #80/ }));
    const status = screen.getByLabelText("Status") as HTMLSelectElement;
    expect(status.value).toBe("void");
  });

  it("still shows blocked as the current status of a blocked task", async () => {
    state.status = "blocked";
    render(<TaskPeek />);
    fireEvent.click(await screen.findByRole("button", { name: /Edit task #80/ }));
    const status = screen.getByLabelText("Status") as HTMLSelectElement;
    expect(status.value).toBe("blocked");
  });
});
