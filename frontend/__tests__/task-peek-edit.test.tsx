import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/** The peek is "the one landing place for every task reference" and carried
 *  exactly one control (Delegate). Changing a status or a due date meant
 *  leaving for Browse, whose edit row holds three of these fields — and
 *  marking a task done existed only on My Day, for your own tasks. */

const mocks = vi.hoisted(() => ({ api: vi.fn() }));

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: mocks.api };
});
vi.mock("next/navigation", () => ({ usePathname: () => "/" }));

import { TaskPeek } from "@/components/task-peek";

const task = {
  id: 4,
  title: "harden the importer",
  status: "in_progress",
  priority: "high",
  assignee: "ava",
  due_date: "2026-08-25",
};

beforeEach(() => {
  vi.clearAllMocks();
  mocks.api.mockImplementation((path: string, opts?: { method?: string }) => {
    if (opts?.method === "PATCH") return Promise.resolve({ id: 4 });
    if (path.endsWith("/worklog")) return Promise.resolve([]);
    if (path === "/api/users") return Promise.resolve([]);
    return Promise.resolve({ ...task });
  });
  window.history.replaceState({}, "", "/?task=4");
});

const patchCalls = () =>
  mocks.api.mock.calls.filter(([, o]) => o?.method === "PATCH");

describe("editing from the task panel", () => {
  it("sends only the fields the editor changed", async () => {
    render(<TaskPeek />);
    fireEvent.click(await screen.findByRole("button", { name: /edit…/ }));

    fireEvent.change(screen.getByLabelText("Status"), {
      target: { value: "todo" },
    });
    fireEvent.change(screen.getByLabelText("Due"), {
      target: { value: "2026-09-01" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(patchCalls()).toHaveLength(1));
    const [path, opts] = patchCalls()[0];
    expect(path).toBe("/api/tasks/4");
    // an untouched field written back would silently revert a concurrent edit
    expect(JSON.parse(opts.body)).toEqual({
      status: "todo",
      due_date: "2026-09-01",
    });
  });

  it("marks a task done in one click", async () => {
    render(<TaskPeek />);
    fireEvent.click(await screen.findByRole("button", { name: /mark done/ }));
    await waitFor(() => expect(patchCalls()).toHaveLength(1));
    expect(JSON.parse(patchCalls()[0][1].body)).toEqual({ status: "done" });
  });

  it("voids only through the confirm, and says what void means first", async () => {
    render(<TaskPeek />);
    fireEvent.click(await screen.findByRole("button", { name: /void…/ }));
    // the consequence is stated and nothing is sent yet
    expect(screen.getByText(/leaves every list, metric and search result/)).toBeTruthy();
    expect(patchCalls()).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Void task" }));
    await waitFor(() => expect(patchCalls()).toHaveLength(1));
    expect(JSON.parse(patchCalls()[0][1].body)).toEqual({ status: "void" });
  });

  it("offers restore on a voided task", async () => {
    mocks.api.mockImplementation((path: string, opts?: { method?: string }) => {
      if (opts?.method === "PATCH") return Promise.resolve({ id: 4 });
      if (path.endsWith("/worklog")) return Promise.resolve([]);
      if (path === "/api/users") return Promise.resolve([]);
      return Promise.resolve({ ...task, status: "void" });
    });
    render(<TaskPeek />);
    fireEvent.click(
      await screen.findByRole("button", { name: /restore to todo/ }),
    );
    await waitFor(() => expect(patchCalls()).toHaveLength(1));
    expect(JSON.parse(patchCalls()[0][1].body)).toEqual({ status: "todo" });
    expect(screen.queryByRole("button", { name: /void…/ })).toBeNull();
  });

  it("keeps status and done off a delegated task", async () => {
    task.status = "in_progress";
    mocks.api.mockImplementation((path: string) =>
      path.endsWith("/worklog")
        ? Promise.resolve([])
        : path === "/api/users"
          ? Promise.resolve([])
          : Promise.resolve({ ...task, delegated_agent: "scout", sponsor: "ava" }),
    );
    render(<TaskPeek />);
    fireEvent.click(await screen.findByRole("button", { name: /edit…/ }));
    // the sponsor's verdict is the only path that ends a delegation — a
    // status select here collects an edit the server refuses
    expect(screen.queryByLabelText("Status")).toBeNull();
    expect(screen.queryByRole("button", { name: /mark done/ })).toBeNull();
    expect(screen.getByLabelText("Priority")).toBeTruthy();
  });
});
