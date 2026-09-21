import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
  window.history.replaceState(null, "", "/dashboard");
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
  it("offers the grouped native selector, switches in place, and still lands deep links", async () => {
    render(<Dashboard />);
    const select = await screen.findByRole("combobox", { name: "Browse register" }) as HTMLSelectElement;
    expect(screen.getByLabelText("Browse register", { exact: true })).toBe(select);
    expect(select.value).toBe("browse-tasks");
    expect(Array.from(select.querySelectorAll("optgroup"), (group) => group.label)).toEqual(["Work", "People", "Reference"]);
    expect(screen.queryByRole("navigation", { name: "Section" })).toBeNull();
    expect(screen.queryByRole("navigation", { name: "Browse sections" })).toBeNull();
    expect(select.parentElement?.classList.contains("lg:hidden")).toBe(false);
    expect(Array.from(select.options, (option) => option.value)).toEqual([
      "browse-tasks", "browse-recently-shipped", "browse-engagements", "browse-milestones", "browse-blockers", "browse-open-questions",
      "browse-capacity", "browse-time-away", "browse-calendar", "browse-recent-standups",
      "browse-decisions", "browse-lessons", "browse-knowledge-base", "browse-recent-activity",
    ]);
    const entries = window.history.length;
    fireEvent.change(select, { target: { value: "browse-calendar" } });
    expect(window.location.hash).toBe("#browse-calendar");
    // no landing on a select change: a keyboard reader arrows through the
    // options, and Chrome fires change on every step of a closed select
    expect(document.activeElement?.id).not.toBe("browse-calendar");
    expect(window.history.length).toBe(entries);
    expect(screen.getByRole("heading", { name: "Calendar" })).toBeTruthy();
    fireEvent.change(select, { target: { value: "browse-tasks" } });
    expect(window.location.hash).toBe("#browse-tasks");
    expect(window.history.length).toBe(entries);
    act(() => window.dispatchEvent(new CustomEvent("skein-hash", { detail: { anchor: "blocker-3" } })));
    expect(select.value).toBe("browse-blockers");
    expect(document.activeElement?.id).toBe("blocker-3");
  });
  it("shows Tasks first and preserves a mounted draft across register changes", async () => {
    render(<Dashboard />);
    const select = await screen.findByRole("combobox", { name: "Browse register" }) as HTMLSelectElement;
    expect(select.value).toBe("browse-tasks");
    expect(screen.queryByRole("heading", { name: "Blockers" })).toBeNull();
    fireEvent.change(screen.getByRole("combobox", { name: "Browse register" }), { target: { value: "browse-milestones" } });
    fireEvent.click(screen.getByText("Add milestone"));
    const title = screen.getByRole("textbox", { name: "New milestone title" });
    fireEvent.change(title, { target: { value: "Keep this draft" } });
    fireEvent.change(select, { target: { value: "browse-tasks" } });
    expect(screen.queryByRole("textbox", { name: "New milestone title" })).toBeNull();
    fireEvent.change(screen.getByRole("combobox", { name: "Browse register" }), { target: { value: "browse-milestones" } });
    expect(screen.getByRole("textbox", { name: "New milestone title" })).toBe(title);
    expect((title as HTMLInputElement).value).toBe("Keep this draft");
  });
  it("selects stable section headings without unmounting the registers", async () => {
    render(<Dashboard />);
    const select = await screen.findByRole("combobox", { name: "Browse register" });
    const heading = document.getElementById("browse-blockers-title");
    expect(heading?.textContent).toBe("Blockers");
    fireEvent.change(select, { target: { value: "browse-blockers" } });
    expect(window.location.hash).toBe("#browse-blockers");
    expect(screen.getByRole("heading", { name: "Blockers" })).toBe(heading);
    expect(document.activeElement?.id).not.toBe("browse-blockers");
  });
});

describe("Browse form metadata", () => {
  it("gives each create control a stable name", async () => {
    render(<Dashboard />);
    for (const [section, summary, fields] of [
      ["Capacity", "Add allocation", [["Person to allocate", "allocate-person"], ["Engagement to allocate to", "allocate-engagement"], ["Percent of their time", "allocate-percent"]]],
      ["Milestones", "Add milestone", [["New milestone title", "milestone-title"], ["Milestone due date", "milestone-due-date"]]],
      ["Calendar", "Add event", [["New event title", "event-title"], ["Event start", "event-start"]]],
    ] as const) {
      fireEvent.change(await screen.findByRole("combobox", { name: "Browse register" }), { target: { value: `browse-${section.toLowerCase()}` } });
      fireEvent.click(screen.getByText(summary));
      expect(fields.map(([label]) => [label, screen.getByLabelText(label).getAttribute("name")])).toEqual(fields);
    }
  });
});

describe("the blocker register", () => {
  it("changes impact in place — the field that sets the escalation clock", async () => {
    render(<Dashboard />);
    fireEvent.change(await screen.findByRole("combobox", { name: "Browse register" }), { target: { value: "browse-blockers" } });
    fireEvent.change(await screen.findByLabelText("Impact of blocker #3"), {
      target: { value: "critical" },
    });
    await waitFor(() => expect(calls("PATCH")).toHaveLength(1));
    expect(calls("PATCH")[0][0]).toBe("/api/blockers/3");
    expect(JSON.parse(calls("PATCH")[0][1].body)).toEqual({ impact: "critical" });
  });

  it("resolves from the register itself", async () => {
    render(<Dashboard />);
    fireEvent.change(await screen.findByRole("combobox", { name: "Browse register" }), { target: { value: "browse-blockers" } });
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
    fireEvent.change(await screen.findByRole("combobox", { name: "Browse register" }), { target: { value: "browse-blockers" } });
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
    fireEvent.change(await screen.findByRole("combobox", { name: "Browse register" }), { target: { value: "browse-milestones" } });
    fireEvent.click(screen.getByText("Add milestone"));
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
    fireEvent.change(await screen.findByRole("combobox", { name: "Browse register" }), { target: { value: "browse-milestones" } });
    fireEvent.click(screen.getByText("Add milestone"));
    await screen.findByLabelText("New milestone title");
    expect(screen.queryByText(/ask the Chief of Staff/)).toBeNull();
  });
});

it("returns focus to blocker assignment on Escape", async () => {
  render(<Dashboard />);
    fireEvent.change(await screen.findByRole("combobox", { name: "Browse register" }), { target: { value: "browse-blockers" } });
  fireEvent.click(await screen.findByRole("button", { name: "Assign blocker #3: vendor contract unsigned" }));
  fireEvent.keyDown(screen.getByLabelText("Give blocker #3 an owner"), { key: "Escape" });
  await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: "Assign blocker #3: vendor contract unsigned" })));
});

it.each(["assign", "answer"])("returns to question %s after Escape", async (mode) => {
  const original = mocks.api.getMockImplementation()!;
  mocks.api.mockImplementation((path: string, opts?: { method?: string }) =>
    path === "/api/questions" ? Promise.resolve([{ id: 1, question: "Do we have budget for a usability test round?", assigned_to: "", status: "open", answer: "", visibility: "workspace", crew_id: 0 }]) : original(path, opts),
  );
  render(<Dashboard />);
  fireEvent.change(await screen.findByRole("combobox", { name: "Browse register" }), { target: { value: "browse-open-questions" } });
  fireEvent.click(await screen.findByRole("button", { name: new RegExp(mode === "assign" ? "unassigned — assign" : "answer…") }));
  fireEvent.keyDown(screen.getByLabelText(mode === "assign" ? "Assign this question to" : "Answer this question"), { key: "Escape" });
  await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: new RegExp(mode === "assign" ? "unassigned — assign" : "answer…") })));
});
