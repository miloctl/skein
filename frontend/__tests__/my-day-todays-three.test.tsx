import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  requests: [] as { path: string; method: string }[],
  briefing: {
    user: "tester",
    date: "2026-08-15",
    attention_total: 0,
    pending_reviews_total: 0,
    attention: [],
    your_work: {
      tasks: [
        { id: 1, title: "Plan launch", priority: "high", status: "todo" },
        {
          id: 2,
          title: "Check evidence",
          priority: "high",
          status: "in_progress",
        },
        { id: 3, title: "Write summary", priority: "medium", status: "todo" },
        { id: 4, title: "Share result", priority: "low", status: "todo" },
      ],
      due_soon: [],
      standup_suggestion: "",
    },
    team: {
      recently_shipped: [],
      escalated_blockers: [],
      todays_events: [],
      recent_activity: [],
    },
  },
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string, init?: RequestInit) => {
      mocks.requests.push({ path, method: init?.method ?? "GET" });
      if (path === "/api/briefing")
        return Promise.resolve({
          ...mocks.briefing,
          your_work: {
            ...mocks.briefing.your_work,
            tasks: mocks.briefing.your_work.tasks.map((task) => ({ ...task })),
          },
        });
      if (path === "/api/onboarding")
        return Promise.resolve({ steps: [], complete: true, progress: "4/4" });
      if (path === "/api/field-guide/hint")
        return Promise.resolve({ suggestion: null, tied_count: 0, total: 1 });
      if (path.startsWith("/api/delta"))
        return Promise.resolve({ since: "", quiet: true, items: [] });
      return Promise.resolve({});
    },
  };
});

vi.mock("next/navigation", () => ({ usePathname: () => "/" }));

import MyDay from "@/app/page";

const storageKey = "skein-todays-three:tester";

beforeEach(() => {
  window.localStorage.clear();
  window.localStorage.setItem("skein-user", "tester");
  window.history.replaceState({}, "", "/");
  mocks.requests = [];
  mocks.briefing.date = "2026-08-15";
});

describe("Today's Three", () => {
  it("keeps a three-task focus without hiding the server-ranked list", async () => {
    render(<MyDay />);

    await screen.findByRole("heading", { level: 3, name: "Today's Three" });
    // re-derived on every read, never held across a click: each click is a
    // discrete event React flushes on its own, and `within` a section node
    // React replaced during one of them searches a detached tree — the count
    // is on the page and the query still fails, which reads as the selection
    // never landing.
    const section = () =>
      screen
        .getByRole("heading", { level: 3, name: "Today's Three" })
        .closest("section") as HTMLElement;
    expect(within(section()).getByText("0/3")).toBeTruthy();

    for (const id of [1, 2, 3]) {
      fireEvent.click(
        screen.getByRole("button", {
          name: `Add task #${id} to Today's Three`,
        }),
      );
    }

    expect(within(section()).getByText("3/3")).toBeTruthy();
    expect(
      JSON.parse(window.localStorage.getItem(storageKey) ?? "null"),
    ).toEqual({
      team_date: "2026-08-15",
      task_ids: [1, 2, 3],
    });
    expect(
      mocks.requests.filter(
        (request) => request.path === "/api/field-guide/todays-three",
      ),
    ).toHaveLength(1);
    expect(screen.getAllByText("Plan launch")).toHaveLength(2);
    expect(screen.getByText("Share result")).toBeTruthy();

    fireEvent.click(
      screen.getByRole("button", { name: "Add task #4 to Today's Three" }),
    );
    expect(
      JSON.parse(window.localStorage.getItem(storageKey) ?? "null").task_ids,
    ).toEqual([1, 2, 3]);

    fireEvent.click(
      screen.getByRole("button", { name: "Remove task #2 from Today's Three" }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Add task #4 to Today's Three" }),
    );
    expect(
      JSON.parse(window.localStorage.getItem(storageKey) ?? "null").task_ids,
    ).toEqual([1, 3, 4]);

    fireEvent.click(
      within(section())
        .getByText("Plan launch")
        .closest("button") as HTMLElement,
    );
    expect(window.location.search).toBe("?task=1");
  });

  it("removes duplicate, invalid, and unavailable task ids from storage", async () => {
    window.localStorage.setItem(
      storageKey,
      JSON.stringify({
        team_date: "2026-08-15",
        task_ids: [2, 99, 2, "1", 3, 4, 1],
      }),
    );

    render(<MyDay />);

    const heading = await screen.findByRole("heading", {
      level: 3,
      name: "Today's Three",
    });
    expect(
      within(heading.closest("section") as HTMLElement).getByText("3/3"),
    ).toBeTruthy();
    await waitFor(() =>
      expect(
        JSON.parse(window.localStorage.getItem(storageKey) ?? "null"),
      ).toEqual({
        team_date: "2026-08-15",
        task_ids: [2, 3, 4],
      }),
    );
  });

  it("overwrites the one user payload when the team date changes", async () => {
    window.localStorage.setItem(
      storageKey,
      JSON.stringify({ team_date: "2026-08-14", task_ids: [1, 2] }),
    );

    render(<MyDay />);

    const heading = await screen.findByRole("heading", {
      level: 3,
      name: "Today's Three",
    });
    expect(
      within(heading.closest("section") as HTMLElement).getByText("0/3"),
    ).toBeTruthy();
    await waitFor(() =>
      expect(
        JSON.parse(window.localStorage.getItem(storageKey) ?? "null"),
      ).toEqual({
        team_date: "2026-08-15",
        task_ids: [],
      }),
    );
    const focusKeys = Array.from(
      { length: window.localStorage.length },
      (_, index) => window.localStorage.key(index) ?? "",
    ).filter((key) => key.startsWith("skein-todays-three:"));
    expect(focusKeys).toEqual([storageKey]);
  });

  it("does not write an older team date over a newer tab payload", async () => {
    render(<MyDay />);
    await screen.findByRole("heading", { level: 3, name: "Today's Three" });
    const newer = { team_date: "2026-08-16", task_ids: [1] };

    window.localStorage.setItem(storageKey, JSON.stringify(newer));
    fireEvent(window, new Event("storage"));

    await waitFor(() =>
      expect(JSON.parse(window.localStorage.getItem(storageKey) ?? "null")).toEqual(newer),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Add task #2 to Today's Three" }),
    );
    expect(JSON.parse(window.localStorage.getItem(storageKey) ?? "null")).toEqual(newer);
  });
});
