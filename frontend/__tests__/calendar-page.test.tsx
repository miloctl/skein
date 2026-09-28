import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** Work → Calendar: one grid of everything with a date, placed on the team
 *  day the server names, never the browser's. */

// A team seven hours west of UTC, Los Angeles in October. The server stores
// starts_at in UTC and answers starts_local on the team clock
// (services/schedule.py::with_local), and the two differ: a grid that reads
// starts_at puts a meeting at the wrong hour, and an evening one on the wrong
// day. Times below are written on the team clock.
const utc = (local: string | null) =>
  local && local.length > 10
    ? new Date(Date.parse(`${local}:00Z`) + 7 * 3_600_000).toISOString().slice(0, 16)
    : local;

// the shapes GET /api/calendar and GET /api/events/{id} answer
// (services/schedule.py::calendar_range and with_local)
const event = (id: number, title: string, starts_local: string, ends_local: string | null = null) => ({
  id,
  title,
  starts_at: utc(starts_local) as string,
  ends_at: utc(ends_local),
  starts_local,
  ends_local,
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
const patches: Record<string, unknown>[] = [];
const deletes: string[] = [];

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    getUser: () => "ava",
    api: async (path: string, init?: RequestInit) => {
      calls.push(path);
      if (init?.method === "DELETE") {
        deletes.push(path);
        return {};
      }
      if (path === "/api/events/1/links") {
        posts.push(JSON.parse(String(init?.body)));
        return {};
      }
      if (path === "/api/events/1/items")
        return { decision: [{ id: 4, title: "ship on friday" }], task: [{ id: 5, title: "book the room" }] };
      if (init?.method === "PATCH") {
        patches.push(JSON.parse(String(init.body)));
        return { updated: [] };
      }
      if (init?.method === "POST" && path === "/api/events") {
        posts.push(JSON.parse(String(init.body)));
        return { id: 99 };
      }
      if (path.startsWith("/api/calendar?")) {
        const q = new URLSearchParams(path.split("?")[1]);
        const inRange = [...events.values()].filter(
          (e) =>
            e.starts_local.slice(0, 10) <= q.get("end")! && e.starts_local.slice(0, 10) >= q.get("start")!,
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
  patches.length = 0;
  deletes.length = 0;
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

  it("puts an evening meeting on its team day, at its team hour", async () => {
    // 20:00 in Los Angeles is 03:00 the next day in UTC
    events.set(3, event(3, "Late call", "2026-10-14T20:00", "2026-10-14T21:00"));
    render(<CalendarPage />);
    const late = await screen.findByRole("button", { name: "20:00 Late call" });
    expect(within(day("Wednesday, October 14")).getByRole("button", { name: "20:00 Late call" })).toBe(late);
    expect(within(day("Thursday, October 15")).queryByText(/Late call/)).toBeNull();
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

  it("edits only what changed, and a cleared field travels as a dash", async () => {
    events.set(1, { ...event(1, "Planning sync", "2026-10-14T10:00", "2026-10-14T11:00"), description: "weekly" });
    render(<CalendarPage />);
    fireEvent.click(await screen.findByRole("button", { name: "10:00 Planning sync" }));
    const dialog = await screen.findByRole("dialog", { name: "Event: Planning sync" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Edit event: Planning sync" }));
    // Escape leaves the form, not the panel
    fireEvent.keyDown(within(dialog).getByLabelText("Title"), { key: "Escape" });
    expect(screen.getByRole("dialog")).toBeTruthy();
    await waitFor(() => expect(document.activeElement?.id).toBe("event-edit"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Edit event: Planning sync" }));
    fireEvent.change(within(dialog).getByLabelText("Title"), { target: { value: "Planning" } });
    fireEvent.change(within(dialog).getByLabelText("Description"), { target: { value: "" } });
    await act(async () => {
      fireEvent.click(within(dialog).getByRole("button", { name: "Save event" }));
    });
    expect(patches).toEqual([{ title: "Planning", description: "-" }]);
  });

  it("lists what came out of the meeting, and links and unlinks a record", async () => {
    render(<CalendarPage />);
    fireEvent.click(await screen.findByRole("button", { name: "10:00 Planning sync" }));
    const dialog = await screen.findByRole("dialog", { name: "Event: Planning sync" });
    expect(await within(dialog).findByRole("link", { name: "ship on friday" })).toBeTruthy();
    // a task opens the task peek over the panel
    expect(within(dialog).getByRole("button", { name: /book the room/ })).toBeTruthy();
    expect(within(dialog).getByRole("link", { name: "Paste notes for this meeting" }).getAttribute("href")).toBe(
      "/ingest?event=1",
    );
    fireEvent.change(within(dialog).getByLabelText("Kind"), { target: { value: "question" } });
    fireEvent.change(within(dialog).getByLabelText("Record number"), { target: { value: "12" } });
    await act(async () => {
      fireEvent.click(within(dialog).getByRole("button", { name: "Link a record" }));
    });
    expect(posts).toEqual([{ kind: "question", item_id: 12 }]);
    await act(async () => {
      fireEvent.click(within(dialog).getByRole("button", { name: "Unlink decision #4 from this meeting" }));
    });
    expect(deletes).toEqual(["/api/events/1/links/decision/4"]);
  });

  it("links the agenda's references the server says this reader may open", async () => {
    events.set(1, {
      ...event(1, "Planning sync", "2026-10-14T10:00", "2026-10-14T11:00"),
      agenda: "Review question #3, then question #9",
      // #9 is absent: the reader may not open it (services/refs.py::readable_refs)
      agenda_refs: [{ entity: "question", id: 3, title: "who owns it?" }],
    } as ReturnType<typeof event>);
    render(<CalendarPage />);
    fireEvent.click(await screen.findByRole("button", { name: "10:00 Planning sync" }));
    const dialog = await screen.findByRole("dialog", { name: "Event: Planning sync" });
    const link = await within(dialog).findByRole("link", { name: "question #3" });
    expect(link.getAttribute("href")).toBe("/dashboard#question-3");
    expect(within(dialog).queryByRole("link", { name: "question #9" })).toBeNull();
    expect(within(dialog).getByText(/question #9/)).toBeTruthy();
  });

  it("saves a one-day all-day event as timed, with its stored end cleared", async () => {
    events.set(4, event(4, "Holiday", "2026-10-05", "2026-10-06"));
    render(<CalendarPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Holiday" }));
    const dialog = await screen.findByRole("dialog", { name: "Event: Holiday" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Edit event: Holiday" }));
    fireEvent.click(within(dialog).getByLabelText("All day"));
    await act(async () => {
      fireEvent.click(within(dialog).getByRole("button", { name: "Save event" }));
    });
    // "" means unchanged to the server, and the date end it kept is of the
    // other kind, which it refuses
    expect(patches).toEqual([{ starts_at: "2026-10-05T09:00", ends_at: "-" }]);
  });

  it("takes All day from the start shown, and keeps an event's length when it moves", async () => {
    render(<CalendarPage />);
    await screen.findByText("Planning sync", { exact: false });
    fireEvent.click(screen.getByRole("button", { name: "Add an event on Monday, October 5" }));
    fireEvent.change(screen.getByLabelText("Starts (team clock)"), { target: { value: "2026-10-08T09:00" } });
    fireEvent.click(screen.getByLabelText("All day"));
    expect((screen.getByLabelText("First day") as HTMLInputElement).value).toBe("2026-10-08");

    fireEvent.click(await screen.findByRole("button", { name: "10:00 Planning sync" }));
    const dialog = await screen.findByRole("dialog", { name: "Event: Planning sync" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Edit event: Planning sync" }));
    fireEvent.change(within(dialog).getByLabelText("Starts (team clock)"), {
      target: { value: "2026-10-16T14:00" },
    });
    await act(async () => {
      fireEvent.click(within(dialog).getByRole("button", { name: "Save event" }));
    });
    expect(patches).toEqual([{ starts_at: "2026-10-16T14:00", ends_at: "2026-10-16T15:00" }]);
  });

  it("gives an agenda link to another meeting a fresh panel", async () => {
    events.set(1, {
      ...event(1, "Planning sync", "2026-10-14T10:00", "2026-10-14T11:00"),
      agenda: "then event #7",
      agenda_refs: [{ entity: "event", id: 7, title: "Vendor call" }],
    } as ReturnType<typeof event>);
    events.set(7, event(7, "Vendor call", "2026-10-16T09:00"));
    const { rerender } = render(<CalendarPage />);
    fireEvent.click(await screen.findByRole("button", { name: "10:00 Planning sync" }));
    const dialog = await screen.findByRole("dialog", { name: "Event: Planning sync" });
    expect(within(dialog).getByRole("link", { name: "event #7" }).getAttribute("href")).toBe(
      "/calendar?event=7",
    );
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete event: Planning sync" }));
    // the link navigates as next/link does: a pushed entry this page did not mark
    act(() => window.history.pushState({}, "", "/calendar?event=7"));
    rerender(<CalendarPage />);
    const next = await screen.findByRole("dialog", { name: "Event: Vendor call" });
    // a confirmation opened for the last meeting must not delete this one
    expect(within(next).queryByRole("button", { name: "Delete event" })).toBeNull();
    fireEvent.click(within(next).getByRole("button", { name: "Close the event panel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(window.location.search).toBe("");
  });

  it("waits under a task peek that a reload opened with it", async () => {
    window.history.replaceState(null, "", "/calendar?event=1&task=4");
    render(<CalendarPage />);
    const dialog = await screen.findByRole("dialog", { name: "Event: Planning sync" });
    const layer = dialog.parentElement!;
    expect(layer.hasAttribute("inert")).toBe(true);
    // the peek closes: components/task-peek.tsx drops ?task= and says so
    act(() => {
      window.history.replaceState(null, "", "/calendar?event=1");
      window.dispatchEvent(new CustomEvent("skein-peek-close", { detail: { taskId: 4 } }));
    });
    expect(layer.hasAttribute("inert")).toBe(false);
    expect(document.activeElement).toBe(within(dialog).getByRole("button", { name: "Close the event panel" }));
  });

  it("lets one Escape close only a task peek that a reload opened with it", async () => {
    // components/task-peek.tsx registers first on a reload and drops ?task= at
    // once for a peek that opened from a link
    const peek = (e: KeyboardEvent) => {
      if (e.key === "Escape") window.history.replaceState({}, "", "/calendar?event=1");
    };
    document.addEventListener("keydown", peek);
    try {
      window.history.replaceState(null, "", "/calendar?event=1&task=4");
      render(<CalendarPage />);
      await screen.findByRole("dialog", { name: "Event: Planning sync" });
      fireEvent.keyDown(document, { key: "Escape" });
      expect(screen.getByRole("dialog", { name: "Event: Planning sync" })).toBeTruthy();
    } finally {
      document.removeEventListener("keydown", peek);
    }
  });

  it("gives focus back only when the panel holds it", async () => {
    // a task peek on top holds focus while the panel below unmounts, and
    // React's development double run of the panel's effect does the same
    const { rerender } = render(<CalendarPage />);
    fireEvent.click(await screen.findByRole("button", { name: "10:00 Planning sync" }));
    await screen.findByRole("dialog", { name: "Event: Planning sync" });
    const elsewhere = document.createElement("button");
    document.body.appendChild(elsewhere);
    try {
      elsewhere.focus();
      act(() => window.history.replaceState(null, "", "/calendar"));
      rerender(<CalendarPage />);
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
      expect(document.activeElement).toBe(elsewhere);
    } finally {
      elsewhere.remove();
    }
  });

  it("keeps focus on a control after a cancelled delete and after an add", async () => {
    render(<CalendarPage />);
    fireEvent.click(await screen.findByRole("button", { name: "10:00 Planning sync" }));
    const dialog = await screen.findByRole("dialog", { name: "Event: Planning sync" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete event: Planning sync" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel deletion" }));
    await waitFor(() =>
      expect(document.activeElement).toBe(
        within(dialog).getByRole("button", { name: "Delete event: Planning sync" }),
      ),
    );
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    fireEvent.click(screen.getByRole("button", { name: "Add an event" }));
    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Retro" } });
    fireEvent.change(screen.getByLabelText("Starts (team clock)"), { target: { value: "2026-10-20T15:00" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Add event" }));
    });
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByRole("button", { name: "Add an event" })),
    );
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
