import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

/** The provenance chain in the task panel is read on its first open. After an
 *  edit in the same panel, the chain must show that edit, and it must stay
 *  open while it rereads. */

const state = vi.hoisted(() => ({ edits: 0 }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string, init?: RequestInit) => {
      if (init?.method === "PATCH") {
        state.edits += 1;
        return Promise.resolve({});
      }
      if (path.includes("/worklog")) return Promise.resolve([]);
      if (path === "/api/provenance/task/4")
        return Promise.resolve({
          origin: "human",
          created_by: "ava",
          created_at: "2026-09-20T09:00:00+00:00",
          proposal: null,
          verdict_is_weak: false,
          history: Array.from({ length: state.edits }, () => ({
            actor: "ava",
            action: "update_task",
            created_at: "2026-09-21T09:00:00+00:00",
          })),
        });
      if (path.startsWith("/api/tasks/4"))
        return Promise.resolve({ id: 4, title: "Build the happy path", status: "todo", priority: "high" });
      return Promise.resolve([]);
    },
  };
});

import { TaskPeek } from "@/components/task-peek";
import { dismissStatus } from "@/lib/status";

afterEach(() => {
  state.edits = 0;
  act(() => dismissStatus());
});

describe("the provenance chain in the task panel", () => {
  it("shows an edit made after the chain was first read", async () => {
    window.history.replaceState({}, "", "/?task=4");
    render(<TaskPeek />);
    fireEvent.click(await screen.findByRole("button", { name: "where did this come from?" }));
    expect(await screen.findByText("No change is recorded since then.")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /Mark task #4/ }));
    expect(await screen.findByText(/ava update task/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "hide the chain" })).toBeTruthy();
  });
});
