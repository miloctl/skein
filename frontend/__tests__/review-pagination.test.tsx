import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { state } = vi.hoisted(() => ({
  state: {
    rows: [] as {
      id: number;
      entity: string;
      entity_id: null;
      action: string;
      payload: { title: string };
      summary: string;
      proposed_by: string;
      requested_by: null;
      origin: string;
      created_at: string;
      label: string;
    }[],
    requests: [] as { path: string; body?: string }[],
  },
}));

const makeRows = (count: number) =>
  Array.from({ length: count }, (_, index) => ({
    id: index + 1,
    entity: "task",
    entity_id: null,
    action: "create",
    payload: { title: `task ${index + 1}` },
    summary: `proposal ${index + 1}`,
    proposed_by: "scout",
    requested_by: null,
    origin: "agent",
    created_at: "2026-08-15T09:00:00+00:00",
    label: "add a task",
  }));

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    getUser: () => "tester",
    subscribeUser: () => () => {},
    api: (path: string, options?: { body?: string }) => {
      state.requests.push({ path, body: options?.body });
      if (path.startsWith("/api/review?status=pending")) {
        const query = new URL(path, "http://skein.test").searchParams;
        const after = Number(query.get("after") ?? 0);
        const limit = Number(query.get("limit") ?? 50);
        return Promise.resolve(
          state.rows.filter((row) => row.id > after).slice(0, limit),
        );
      }
      return Promise.resolve([]);
    },
  };
});

vi.mock("next/navigation", () => ({ usePathname: () => "/review" }));

import ReviewPage from "@/app/review/page";

beforeEach(() => {
  window.history.replaceState({}, "", "/review");
  state.rows = makeRows(55);
  state.requests = [];
});

describe("the pending review cursor", () => {
  it("appends every later proposal from the FIFO queue", async () => {
    render(<ReviewPage />);
    await screen.findByText("proposal 50");
    expect(screen.queryByText("proposal 51")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "More proposals" }));

    expect(await screen.findByText("proposal 55")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "More proposals" })).toBeNull();
  });

  it("loads through the page that contains a linked proposal and focuses it", async () => {
    window.history.replaceState({}, "", "/review?id=55");
    render(<ReviewPage />);

    const card = await screen.findByLabelText("Proposal #55: add a task");
    await waitFor(() => expect(document.activeElement).toBe(card));
  });

  it("does not scan the queue for a linked proposal that is not pending", async () => {
    window.history.replaceState({}, "", "/review?id=999");
    render(<ReviewPage />);
    await screen.findByText("proposal 50");

    const pending = state.requests.filter((request) =>
      request.path.startsWith("/api/review?status=pending"),
    );
    expect(pending.map((request) => request.path)).toEqual([
      "/api/review?status=pending&limit=1&after=998",
      "/api/review?status=pending&limit=50",
    ]);
  });

  it("marks a deep-linked queue as seen in endpoint-sized pages", async () => {
    state.rows = makeRows(205);
    window.history.replaceState({}, "", "/review?id=205");
    render(<ReviewPage />);
    await screen.findByText("proposal 205");

    const batches = state.requests
      .filter((request) => request.path === "/api/review/seen")
      .map((request) => JSON.parse(request.body ?? "{}").ids as number[]);
    expect(batches.length).toBe(5);
    expect(batches.every((ids) => ids.length <= 200)).toBe(true);
    expect(batches.flat()).toHaveLength(205);
  });

  it("stops a batch selection at the server limit", async () => {
    state.rows = makeRows(201);
    window.history.replaceState({}, "", "/review?id=201");
    render(<ReviewPage />);
    await screen.findByText("proposal 201");

    const boxes = screen.getAllByRole("checkbox") as HTMLInputElement[];
    act(() => boxes.slice(0, 200).forEach((box) => box.click()));
    expect(boxes[200].disabled).toBe(true);
    expect(
      screen.getByText("Select at most 200 proposals at one time."),
    ).toBeTruthy();
  }, 15_000);
});
