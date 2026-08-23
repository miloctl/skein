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
    batchResults: [] as { id: number; status: string; detail?: string }[],
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
      if (path === "/api/review/approve-batch") {
        return Promise.resolve({ results: state.batchResults });
      }
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
  state.batchResults = [];
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

  it("keeps the fetched pages after a verdict, and moves focus to the next row", async () => {
    render(<ReviewPage />);
    await screen.findByText("proposal 50");
    fireEvent.click(screen.getByRole("button", { name: "More proposals" }));
    await screen.findByText("proposal 55");

    const approvals = screen.getAllByRole("button", { name: "Approve" });
    // proposal 52, on the page the reviewer had to fetch: reloading from the
    // first page would take 51..55 away with it
    fireEvent.click(approvals[51]);

    await waitFor(() => expect(screen.queryByText("proposal 52")).toBeNull());
    expect(screen.getByText("proposal 55")).toBeTruthy();
    expect(screen.getByText("proposal 1")).toBeTruthy();
    await waitFor(() =>
      expect(document.activeElement).toBe(
        screen.getByLabelText("Proposal #53: add a task"),
      ),
    );
  });

  it("keeps a forbidden batch row and removes only approved rows", async () => {
    state.rows = makeRows(2);
    state.batchResults = [
      { id: 1, status: "approved" },
      { id: 2, status: "forbidden", detail: "A delivery manager must approve this." },
    ];
    render(<ReviewPage />);
    await screen.findByText("proposal 2");
    screen.getAllByRole("checkbox").forEach((box) => fireEvent.click(box));
    fireEvent.click(screen.getByRole("button", { name: /Approve selected/ }));

    await waitFor(() => expect(screen.queryByText("proposal 1")).toBeNull());
    expect(screen.getByText("proposal 2")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain(
      "#2: A delivery manager must approve this.",
    );
  });

  it("stops a batch selection at the server limit", async () => {
    state.rows = makeRows(201);
    window.history.replaceState({}, "", "/review?id=201");
    render(<ReviewPage />);
    await screen.findByText("proposal 201");

    const boxes = screen.getAllByRole("checkbox") as HTMLInputElement[];
    act(() => boxes.slice(0, 200).forEach((box) => box.click()));
    // re-read the row rather than the handle captured before the clicks: each
    // of those 200 clicks is a discrete event React flushes on its own, and a
    // node React replaced during any of them keeps the `disabled` it had at
    // capture time, so the assertion reports the cap missing when it is there.
    const cap = screen.getAllByRole("checkbox") as HTMLInputElement[];
    expect(cap[200].disabled).toBe(true);
    expect(
      screen.getByText("Select at most 200 proposals at one time."),
    ).toBeTruthy();
    // 200 clicks over a 201-row list is ~6s of re-rendering on an idle
    // machine, and this is the slowest test in the suite. The default ceiling
    // is 15s, which a loaded CI runner crosses — measured by running the suite
    // against saturated cores, where this is the only test that times out.
  }, 30_000);
});
