import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** Work → Calendar: one grid of everything with a date, placed on the team
 *  day the server names, never the browser's. */

// the shapes GET /api/calendar and GET /api/events/{id} answer
// (services/schedule.py::calendar_range and with_local)
const event = (id: number, title: string, starts_at: string, ends_at: string | null = null) => ({
  id,
  title,
  starts_at,
  ends_at,
  starts_local: starts_at,
  ends_local: ends_at,
  description: "",
  attendees: "",
  agenda: "",
  visibility: "workspace",
  crew_id: null,
  engagement_id: null,
  outcome_status: "pending",
});

let today = "2026-10-15";
const events = new Map<number, ReturnType<typeof event>>();
const calls: string[] = [];
const posts: Record<string, unknown>[] = [];

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    getUser: () => "ava",
    api: async (path: string, init?: RequestInit) => {
      calls.push(path);
      if (init?.method === "POST" && path === "/api/events") {
        posts.push(JSON.parse(String(init.body)));
        return { id: 99 };
      }
      if (path.startsWith("/api/calendar?")) {
        const q = new URLSearchParams(path.split("?")[1]);
        const inRange = [...events.values()].filter(
          (e) => e.starts_at.slice(0, 10) <= q.get("end")! && e.starts_at.slice(0, 10) >= q.get("start")!,
        );
        return {
          start: q.get("start"),
          end: q.get("end"),
          today,
          events: inRange,
          tasks:
            q.get("mine") === "true"
              ? [{ id: 5, title: "ship it", due_date: "2026-10-16", assignee: "ava" }]
              : [
                  { id: 5, title: "ship it", due_date: "2026-10-16", assignee: "ava" },
                  { id: 6, title: "ben's task", due_date: "2026-10-16", assignee: "ben" },
                ],
          milestones: [{ id: 7, title: "beta", due_date: "2026-10-20" }],
          promises: [{ id: 8, promise: "slides", to_whom: "Acme", due_date: "2026-10-21", direction: "received" }],
          time_away: [{ person: "ben", kind: "away", starts_on: "2026-10-22", ends_on: "2026-10-23" }],
          truncated: [],
        };
      }
      const one = path.match(/^\/api\/events\/(\d+)$/);
      if (one) {
        const found = events.get(Number(one[1]));
        if (!found) throw new Error("no event");
        return found;
      }
      return [];
    },
  };
});
vi.mock("next/navigation", () => ({
  usePathname: () => "/calendar",
  useSearchParams: () => new URLSearchParams(window.location.search),
}));

import CalendarPage from "@/app/calendar/page";

const day = (label: string) => screen.getByRole("heading", { name: new RegExp(`^${label}`) }).closest("li")!;

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(2026, 9, 15, 12));
  window.history.replaceState(null, "", "/calendar");
  today = "2026-10-15";
  events.clear();
  events.set(1, event(1, "Planning sync", "2026-10-14T10:00", "2026-10-14T11:00"));
  calls.length = 0;
  posts.length = 0;
});
afterEach(() => vi.useRealTimers());

describe("the Calendar page", () => {
  it("places every kind on its day, and narrows tasks to the reader's own by default", async () => {
    render(<CalendarPage />);
    await screen.findByText("Planning sync", { exact: false });
    expect(calls.find((c) => c.startsWith("/api/calendar?"))).toContain("mine=true");
    expect(within(day("Wednesday, October 14")).getByRole("button", { name: "10:00 Planning sync" })).toBeTruthy();
    expect(within(day("Friday, October 16")).getByText("ship it")).toBeTruthy();
    expect(within(day("Tuesday, October 20")).getByText("beta")).toBeTruthy();
    // a received promise says so: under "Promised" it reads as the reader's own
    expect(within(day("Wednesday, October 21")).getByText("Awaiting:", { exact: false })).toBeTruthy();
    expect(within(day("Friday, October 23")).getByText("ben: away")).toBeTruthy();

    fireEvent.click(screen.getByLabelText("Only my tasks"));
    expect(await screen.findByText("ben's task")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Tasks"));
    expect(screen.queryByText("ship it")).toBeNull();
  });

  it("draws an all-day event up to, not on, its exclusive end", async () => {
    events.set(2, event(2, "Offsite", "2026-10-05", "2026-10-07"));
    render(<CalendarPage />);
    await screen.findAllByText("Offsite");
    expect(within(day("Monday, October 5")).getByText("Offsite")).toBeTruthy();
    expect(within(day("Tuesday, October 6")).getByText("Offsite")).toBeTruthy();
    expect(within(day("Wednesday, October 7")).queryByText("Offsite")).toBeNull();
  });

  it("moves to the team's month when the browser's day is in another one", async () => {
    // 20:00 on October 31 in the browser, already November 1 for the team
    vi.setSystemTime(new Date(2026, 9, 31, 20));
    today = "2026-11-01";
    render(<CalendarPage />);
    expect(await screen.findByRole("heading", { name: "November 2026" })).toBeTruthy();
    expect(calls.filter((c) => c.startsWith("/api/calendar?")).at(-1)).toContain("start=2026-10-26");
  });

  it("opens an event in a dialog and gives focus back on Escape", async () => {
    render(<CalendarPage />);
    const button = await screen.findByRole("button", { name: "10:00 Planning sync" });
    button.focus();
    fireEvent.click(button);
    const dialog = await screen.findByRole("dialog", { name: "Event: Planning sync" });
    expect(within(dialog).getByText("Wednesday, October 14, 10:00 to 11:00")).toBeTruthy();
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(document.activeElement).toBe(button);
  });

  it("follows a link to an event in another month", async () => {
    events.set(9, event(9, "Kickoff", "2027-01-20T10:00"));
    window.history.replaceState(null, "", "/calendar?event=9");
    render(<CalendarPage />);
    expect(await screen.findByRole("dialog", { name: "Event: Kickoff" })).toBeTruthy();
    expect(await screen.findByRole("heading", { name: "January 2027" })).toBeTruthy();
  });

  it("files an all-day event with its end one day after the last day", async () => {
    render(<CalendarPage />);
    await screen.findByText("Planning sync", { exact: false });
    fireEvent.click(screen.getByRole("button", { name: "Add an event on Monday, October 5" }));
    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Retreat" } });
    fireEvent.click(screen.getByLabelText("All day"));
    fireEvent.change(screen.getByLabelText("Last day (optional)"), { target: { value: "2026-10-06" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Add event" }));
    });
    expect(posts).toEqual([
      expect.objectContaining({ title: "Retreat", starts_at: "2026-10-05", ends_at: "2026-10-07" }),
    ]);
  });
});
