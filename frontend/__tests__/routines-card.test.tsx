import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/** Plan the week → Routines: the schedule in words, the days as checkboxes,
 *  an agent routine that cannot be private, a paused routine that says why,
 *  and the task panel line that names the routine (docs/intent/routines.md). */

const ROW = {
  id: 7,
  title: "Monday sweep",
  description: "",
  assignee: "",
  agent: "quartermaster",
  acceptance_criteria: "",
  weekdays: "1",
  at_time: "07:00",
  every_weeks: 1,
  starts_on: "2026-09-28",
  due_days: null,
  status: "paused",
  paused_reason: "nobody_finished",
  paused_by: "",
  last_outcome: "previous_open",
  created_by: "mira",
  visibility: "workspace",
  next_local: null,
  last_local: "2026-10-19T07:00",
  open_task_id: 412,
  can_edit: true,
  can_delete: true,
};

const state = vi.hoisted(() => ({
  strong: true,
  rows: [] as unknown[],
  posts: [] as Array<{ path: string; method: string; body: Record<string, unknown> }>,
  task: {} as Record<string, unknown>,
}));

vi.mock("@/lib/audience", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/audience")>();
  return { ...real, useStrongIdentity: () => state.strong };
});
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string, init?: { method?: string; body?: string }) => {
      if (init?.method) {
        state.posts.push({ path, method: init.method, body: JSON.parse(init.body ?? "{}") });
        return Promise.resolve({ id: 8 });
      }
      if (path === "/api/routines") return Promise.resolve({ zone: "Europe/Madrid", routines: state.rows });
      if (path === "/api/agents") return Promise.resolve([{ agent: "quartermaster", delegatable: true }]);
      if (path === "/api/crews" || path === "/api/crews/mine" || path === "/api/users")
        return Promise.resolve([]);
      if (path.endsWith("/worklog") || path.endsWith("/comments")) return Promise.resolve([]);
      if (path.startsWith("/api/tasks/")) return Promise.resolve(state.task);
      return new Promise(() => {});
    },
  };
});

import { RoutinesCard, scheduleWords } from "@/components/routines-card";
import { TaskPeek } from "@/components/task-peek";

beforeEach(() => {
  state.strong = true;
  state.rows = [];
  state.posts = [];
  window.history.replaceState(null, "", "/planning");
});

describe("the routines card", () => {
  it("says a schedule in words", () => {
    expect(scheduleWords({ weekdays: "3", at_time: "10:00", every_weeks: 2 })).toBe(
      "Every 2 weeks on Wednesday at 10:00",
    );
    expect(scheduleWords({ weekdays: "1,4", at_time: "07:00", every_weeks: 1 })).toBe(
      "Every week on Monday and Thursday at 07:00",
    );
  });

  it("sends the checked days as one list", async () => {
    render(<RoutinesCard />);
    expect(
      await screen.findByText("No routines yet. Write the work once, and the week brings it back."),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "New routine" }));
    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Pre-score intake" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "Thursday" }));
    expect(screen.getByText(/^Every week on Monday and Thursday at 09:00, Skein creates/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Save routine" }));
    await waitFor(() => expect(state.posts).toHaveLength(1));
    expect(state.posts[0]).toMatchObject({
      path: "/api/routines",
      method: "POST",
      body: { title: "Pre-score intake", weekdays: "1,4", at_time: "09:00", every_weeks: 1 },
    })
  });

  it("offers no private tier once an agent does the work", async () => {
    render(<RoutinesCard />);
    fireEvent.click(await screen.findByRole("button", { name: "New routine" }));
    const tier = await screen.findByRole("combobox", { name: "Who can see this routine" });
    expect(within(tier).getByRole("option", { name: "only you" })).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Agent"), { target: { value: "quartermaster" } });
    expect(within(tier).queryByRole("option", { name: "only you" })).toBeNull();
    expect(screen.getByText(/An agent routine cannot be private/)).toBeTruthy();
  });

  it("says why a routine paused and links its open task", async () => {
    state.rows = [ROW];
    render(<RoutinesCard />);
    expect(
      await screen.findByText("Paused after 3 skipped times, because the last task is still open."),
    ).toBeTruthy();
    expect(screen.getByText("Skipped Mon 19 Oct: task #412 is still open.")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Task #412/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Resume routine #7" })).toBeTruthy();
  });

  it("asks for a proved identity before it offers a new routine", async () => {
    state.strong = false;
    render(<RoutinesCard />);
    expect(await screen.findByText(/To write a routine, sign in with a personal key/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "New routine" })).toBeNull();
  });
});

describe("the task panel", () => {
  it("names the routine only for a task that carries one", async () => {
    state.task = { id: 412, title: "Monday sweep (2026-10-19)", status: "todo", priority: "medium", routine_id: 7 };
    window.history.pushState({}, "", "?task=412");
    const { unmount } = render(<TaskPeek />);
    expect(await screen.findByText(/Repeats from routine #7\./)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Change it in Plan the week." }).getAttribute("href")).toBe(
      "/planning#planning-routines",
    );
    unmount();
    state.task = { id: 413, title: "By hand", status: "todo", priority: "medium", routine_id: null };
    window.history.pushState({}, "", "?task=413");
    render(<TaskPeek />);
    await screen.findByText("By hand");
    expect(screen.queryByText(/Repeats from routine/)).toBeNull();
  });
});
