import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/** Answer where the ask is read. The question row said "someone is waiting
 *  on the answer" and shipped the reader to Work → Browse to type it —
 *  blocker and meeting rows on the same card already act in place. */

const mocks = vi.hoisted(() => ({ api: vi.fn() }));

const briefing = {
  user: "tester",
  date: "2026-08-20",
  attention_total: 1,
  pending_reviews_total: 0,
  attention: [
    {
      kind: "question",
      ref_id: 1,
      group: "unblock",
      audience: "you",
      label: "question #1: Do we have budget for a usability test round?",
      reason: "assigned to you and still open — someone is waiting on the answer",
      link: "/dashboard#question-1",
    },
    {
      kind: "notification",
      ref_id: 9,
      group: "notice",
      audience: "you",
      label: "agent started on task #32",
      reason: "for you — dismiss when read",
      link: "/agents",
    },
    {
      kind: "notification",
      ref_id: 10,
      group: "notice",
      audience: "you",
      label: "you sponsor task #32",
      reason: "for you — dismiss when read",
      link: "/agents",
    },
  ],
  your_work: { tasks: [], due_soon: [], standup_suggestion: "" },
  team: {
    recently_shipped: [],
    escalated_blockers: [],
    todays_events: [],
    recent_activity: [],
  },
};

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: mocks.api };
});
vi.mock("next/navigation", () => ({ usePathname: () => "/" }));

import MyDay from "@/app/page";
import { getStatus } from "@/lib/status";

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.setItem("skein-user", "tester");
  window.localStorage.setItem("skein-onboarded:tester", "1");
  window.localStorage.setItem("skein-guided-core-done:tester", "1");
  mocks.api.mockImplementation((path: string, opts?: { method?: string }) => {
    if (opts?.method) return Promise.resolve({});
    if (path === "/api/briefing") return Promise.resolve(briefing);
    if (path === "/api/onboarding")
      return Promise.resolve({ steps: [], complete: true, progress: "4/4" });
    if (path.startsWith("/api/field-guide/hint"))
      return Promise.resolve({ suggestion: null, tied_count: 0, total: 1 });
    if (path.startsWith("/api/delta"))
      return Promise.resolve({ since: "", quiet: true, items: [] });
    if (path === "/api/users") return Promise.resolve([]);
    return Promise.resolve({});
  });
});

const posts = () => mocks.api.mock.calls.filter(([, o]) => o?.method);

describe("the question row on My Day", () => {
  it("takes the answer in place", async () => {
    render(<MyDay />);
    fireEvent.click(await screen.findByRole("button", { name: /answer…/ }));
    const input = screen.getByLabelText("Answer question #1");
    fireEvent.keyDown(input, { key: "Enter", target: { value: "Yes — 2 days" } });
    await waitFor(() => expect(posts()).toHaveLength(1));
    const [path, opts] = posts()[0];
    expect(path).toBe("/api/questions/1/answer");
    expect(JSON.parse(opts.body)).toEqual({ answer: "Yes — 2 days" });
  });

  it("reassigns in place", async () => {
    render(<MyDay />);
    fireEvent.click(
      await screen.findByRole("button", {
        name: "Reassign question #1: Do we have budget for a usability test round?",
      }),
    );
    const input = screen.getByLabelText("Assign question #1 to");
    fireEvent.keyDown(input, { key: "Enter", target: { value: "ava" } });
    await waitFor(() => expect(posts()).toHaveLength(1));
    const [path, opts] = posts()[0];
    expect(path).toBe("/api/questions/1");
    expect(JSON.parse(opts.body)).toEqual({ assigned_to: "ava" });
  });
});

describe("the My Day header count", () => {
  it("names the notices beside the judgment count", async () => {
    // "1 thing needs you" over a card visibly holding three rows read as a
    // broken count — the notices the count deliberately excludes are named
    render(<MyDay />);
    expect(await screen.findByText(/1 thing needs you · 2 notices/)).toBeTruthy();
  });
});

describe("My Day action focus", () => {
  it("returns to the answer control after Escape", async () => {
    render(<MyDay />);
    fireEvent.click(await screen.findByRole("button", { name: /answer…/ }));
    fireEvent.keyDown(screen.getByLabelText("Answer question #1"), { key: "Escape" });
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: /answer…/ })));
  });

  it("announces dismissal and focuses the next notice after the row leaves", async () => {
    const original = mocks.api.getMockImplementation()!;
    let dismissed = false;
    mocks.api.mockImplementation((path: string, opts?: { method?: string }) => {
      if (path === "/api/notifications/read") dismissed = true;
      if (path === "/api/briefing" && dismissed)
        return Promise.resolve({ ...briefing, attention: briefing.attention.filter((a) => a.ref_id !== 9) });
      return original(path, opts);
    });
    render(<MyDay />);
    const dismiss = (await screen.findAllByRole("button", { name: /dismiss/i }))[0];
    dismiss.focus();
    fireEvent.click(dismiss);
    await waitFor(() => expect(screen.queryByText("agent started on task #32")).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("link", { name: "you sponsor task #32" })));
    expect(getStatus()?.message).toBe("Notification dismissed.");
  });
});

it.each(["start", "done"])("restores task focus and announces %s", async (action) => {
  const original = mocks.api.getMockImplementation()!;
  let changed = false;
  let finish!: () => void;
  mocks.api.mockImplementation((path: string, opts?: { method?: string }) => {
    if (path === "/api/tasks/1" && opts?.method === "PATCH")
      return new Promise((resolve) => {
        finish = () => { changed = true; resolve({}); };
      });
    if (path === "/api/briefing") return Promise.resolve({
      ...briefing,
      your_work: { ...briefing.your_work, tasks: [
        ...(changed && action === "done" ? [] : [{ id: 1, title: "Plan launch", priority: "high", status: changed ? "in_progress" : "todo" }]),
        { id: 2, title: "Check evidence", priority: "high", status: "in_progress" },
      ] },
    });
    return original(path, opts);
  });
  render(<MyDay />);
  const control = (await screen.findAllByRole("button", { name: new RegExp(`^${action}`) }))[0];
  control.focus();
  fireEvent.click(control);
  expect(control.isConnected).toBe(true);
  await act(async () => { finish(); });
  expect(control.isConnected).toBe(false);
  expect(document.activeElement).toBe(screen.getByRole("button", {
    name: action === "done" ? /^Open.*Check evidence$/ : /^Open.*Plan launch$/,
  }));
  expect(getStatus()?.message).toBe(`Task #1 ${action === "done" ? "done" : "started"}.`);
});

it("does not take focus from a later action when a task write finishes", async () => {
  const original = mocks.api.getMockImplementation()!;
  let finish!: (value: object) => void;
  let changed = false;
  mocks.api.mockImplementation((path: string, opts?: { method?: string }) => {
    if (path === "/api/tasks/1" && opts?.method === "PATCH")
      return new Promise((resolve) => { finish = (value) => { changed = true; resolve(value); }; });
    if (path === "/api/briefing") return Promise.resolve({
      ...briefing, your_work: { ...briefing.your_work, tasks: changed ? [] : [{ id: 1, title: "Plan launch", priority: "high", status: "todo" }] },
    });
    return original(path, opts);
  });
  render(<MyDay />);
  const done = await screen.findByRole("button", { name: /^done/ });
  done.focus();
  fireEvent.click(done);
  const field = screen.getByLabelText("Standup: what are you on today?");
  field.focus();
  finish({});
  await waitFor(() => expect(screen.queryByText("Plan launch")).toBeNull());
  expect(document.activeElement).toBe(field);
});
