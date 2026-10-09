import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/** A notice about a task stayed on My Day after the reader had opened the
 *  task. The panel now posts the source, and announces the change so My Day
 *  and the badge refetch (services/notifications.py::mark_source_read). */

const calls = vi.hoisted(() => [] as Array<{ path: string; method: string; body: unknown }>);

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string, init?: RequestInit) => {
      calls.push({ path, method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : null });
      if (path.includes("worklog")) return Promise.resolve([]);
      if (path === "/api/notifications/read") return Promise.resolve({ marked: 1 });
      if (path === "/api/agents/status") return Promise.resolve({});
      // a pending wake makes the panel poll the task every 2 s
      return Promise.resolve({
        id: 4, title: "Build the path", status: "todo",
        agent_wakeup: { status: "pending" },
      });
    },
  };
});

import { TaskPeek } from "@/components/task-peek";

beforeEach(() => {
  calls.length = 0;
  window.history.replaceState({}, "", "/?task=4");
});

describe("opening a task", () => {
  it("clears the notices about it and announces the change", async () => {
    const announced: string[] = [];
    window.addEventListener("skein-attention-change", () => announced.push("attention"));
    render(<TaskPeek />);
    await screen.findByText("Build the path");
    await waitFor(() =>
      expect(calls).toContainEqual({
        path: "/api/notifications/read",
        method: "POST",
        body: { source_entity: "task", source_id: 4 },
      }),
    );
    await waitFor(() => expect(announced).toEqual(["attention"]));
    // the panel refetches the task while the wake is pending; the notices
    // are cleared ONCE per opened task, not once per fetch
    await waitFor(
      () => expect(calls.filter((c) => c.path.startsWith("/api/tasks/4")).length).toBeGreaterThan(1),
      { timeout: 5000 },
    );
    expect(calls.filter((c) => c.path === "/api/notifications/read")).toHaveLength(1);
    expect(announced).toEqual(["attention"]);
  });
});
