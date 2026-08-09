import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

/** `waiting_on` said what a task was stuck BEHIND and nothing read the other
 *  direction, so typing an edge cost the person who typed it and paid them
 *  nothing back. Unmaintained edges rot, and every synthesis built on them
 *  quietly becomes fiction with receipts. */

const task: Record<string, unknown> = {
  id: 4,
  title: "the dependency",
  status: "in_progress",
  priority: "high",
  assignee: "ava",
};

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string) =>
      path.endsWith("/worklog") ? Promise.resolve([]) : Promise.resolve(task),
  };
});

vi.mock("next/navigation", () => ({ usePathname: () => "/" }));

import { TaskPeek } from "@/components/task-peek";

const open = (extra: Record<string, unknown>) => {
  for (const k of ["unblocks", "unblocks_total", "depth_capped"]) delete task[k];
  Object.assign(task, extra);
  window.history.replaceState({}, "", "/?task=4");
};

describe("what finishing a task releases", () => {
  it("names the tasks that wait on it", async () => {
    open({
      unblocks: [
        { id: 9, title: "the waiter", status: "todo", assignee: "dana" },
        { id: 10, title: "another waiter", status: "todo", assignee: "" },
      ],
      unblocks_total: 2,
      depth_capped: false,
    });
    render(<TaskPeek />);
    expect(await screen.findByText("Finishing this unblocks")).toBeTruthy();
    expect(screen.getByText(/the waiter/)).toBeTruthy();
    expect(screen.getByText(/@dana/)).toBeTruthy();
  });

  it("counts the chain only when it adds to the visible list", async () => {
    open({
      unblocks: [{ id: 9, title: "the waiter", status: "todo", assignee: "" }],
      unblocks_total: 3,
      depth_capped: false,
    });
    render(<TaskPeek />);
    expect(await screen.findByText(/3 tasks in total/)).toBeTruthy();
  });

  it("says nothing about a chain that is only the list itself", async () => {
    open({
      unblocks: [{ id: 9, title: "the waiter", status: "todo", assignee: "" }],
      unblocks_total: 1,
      depth_capped: false,
    });
    render(<TaskPeek />);
    await screen.findByText("Finishing this unblocks");
    expect(screen.queryByText(/in total/)).toBeNull();
  });

  it("admits when the chain runs deeper than it followed", async () => {
    open({
      unblocks: [{ id: 9, title: "the waiter", status: "todo", assignee: "" }],
      unblocks_total: 10,
      depth_capped: true,
    });
    render(<TaskPeek />);
    expect(await screen.findByText(/runs deeper than Skein follows/)).toBeTruthy();
  });

  it("shows no heading at all when nothing waits", async () => {
    // a "Unblocks: nothing" line on every panel would be noise on most tasks
    // to serve the few
    open({ unblocks: [], unblocks_total: 0, depth_capped: false });
    render(<TaskPeek />);
    expect(await screen.findByText("the dependency")).toBeTruthy();
    expect(screen.queryByText("Finishing this unblocks")).toBeNull();
  });
});
