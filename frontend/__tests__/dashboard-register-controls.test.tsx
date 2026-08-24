import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/** Browse's registers were read-only mirrors: the blocker list offered no
 *  claim, no impact change and no resolve (My Day held the only resolve, for
 *  rows that reached YOUR attention list), and the Milestones and Calendar
 *  cards could not create what they listed — their empty states sent the
 *  reader to Chat, where the default mock provider has no such grammar. */

const mocks = vi.hoisted(() => ({ api: vi.fn() }));

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: mocks.api };
});
vi.mock("next/navigation", () => ({ usePathname: () => "/dashboard" }));

import Dashboard from "@/app/dashboard/page";

beforeEach(() => {
  vi.clearAllMocks();
  mocks.api.mockImplementation((path: string, opts?: { method?: string }) => {
    if (opts?.method) return Promise.resolve({});
    if (path === "/api/tasks/browse")
      return Promise.resolve({ open: [], done: [] });
    if (path === "/api/blockers")
      return Promise.resolve([
        {
          id: 3,
          title: "vendor contract unsigned",
          owner: "",
          impact: "medium",
          status: "open",
          visibility: "workspace",
          crew_id: 0,
        },
      ]);
    if (path === "/api/pulse") return Promise.resolve(null);
    return Promise.resolve([]);
  });
});

const calls = (method: string) =>
  mocks.api.mock.calls.filter(([, o]) => o?.method === method);

describe("Browse section navigation", () => {
  it("links to stable section headings without unmounting the registers", async () => {
    render(<Dashboard />);
    const link = await screen.findByRole("link", { name: "Blockers" });
    expect(link.getAttribute("href")).toBe("#browse-blockers");
    expect(document.getElementById("browse-blockers-title")?.textContent).toBe(
      "Blockers",
    );
  });
});

describe("the blocker register", () => {
  it("changes impact in place — the field that sets the escalation clock", async () => {
    render(<Dashboard />);
    fireEvent.change(await screen.findByLabelText("Impact of blocker #3"), {
      target: { value: "critical" },
    });
    await waitFor(() => expect(calls("PATCH")).toHaveLength(1));
    expect(calls("PATCH")[0][0]).toBe("/api/blockers/3");
    expect(JSON.parse(calls("PATCH")[0][1].body)).toEqual({ impact: "critical" });
  });

  it("resolves from the register itself", async () => {
    render(<Dashboard />);
    fireEvent.click(
      await screen.findByRole("button", {
        name: "Resolve blocker #3: vendor contract unsigned",
      }),
    );
    await waitFor(() => expect(calls("POST")).toHaveLength(1));
    expect(calls("POST")[0][0]).toBe("/api/blockers/3/resolve");
  });

  it("gives an unowned blocker an owner", async () => {
    render(<Dashboard />);
    fireEvent.click(
      await screen.findByRole("button", {
        name: "Assign blocker #3: vendor contract unsigned",
      }),
    );
    const input = screen.getByLabelText("Give blocker #3 an owner");
    fireEvent.keyDown(input, { key: "Enter", target: { value: "ava" } });
    await waitFor(() => expect(calls("PATCH")).toHaveLength(1));
    expect(JSON.parse(calls("PATCH")[0][1].body)).toEqual({ owner: "ava" });
  });
});

describe("the milestone and calendar create forms", () => {
  it("files a milestone from the card that lists them", async () => {
    render(<Dashboard />);
    fireEvent.change(await screen.findByLabelText("New milestone title"), {
      target: { value: "Cutover" },
    });
    fireEvent.change(screen.getByLabelText("Milestone due date"), {
      target: { value: "2026-09-01" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add the milestone" }));
    await waitFor(() =>
      expect(calls("POST").some(([p]) => p === "/api/milestones")).toBe(true),
    );
    const [, opts] = calls("POST").find(([p]) => p === "/api/milestones")!;
    expect(JSON.parse(opts.body)).toEqual({
      title: "Cutover",
      due_date: "2026-09-01",
    });
  });

  it("does not send the reader to Chat for what the card can do", async () => {
    render(<Dashboard />);
    await screen.findByLabelText("New milestone title");
    expect(screen.queryByText(/ask the Chief of Staff/)).toBeNull();
  });
});
