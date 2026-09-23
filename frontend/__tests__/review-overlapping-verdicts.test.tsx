import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/** Verdicts resolve in whatever order the network returns them. Each one must
 *  drop its card from the queue as it is NOW, and a judged card must leave the
 *  batch selection too, or the next batch approve reports it as a failure. */

const row = (id: number) => ({
  id,
  entity: "task",
  entity_id: null,
  action: "create",
  payload: { title: `Task ${id}` },
  summary: `create task ${id}`,
  proposed_by: "scout",
  requested_by: null,
  origin: "agent",
  created_at: "2026-08-09T09:00:00+00:00",
  label: `add task ${id}`,
});

let pending: ReturnType<typeof row>[] = [];
const verdicts = new Map<number, () => void>();
const batches: number[][] = [];

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    getUser: () => "tester",
    subscribeUser: () => () => {},
    api: (path: string, init?: RequestInit) => {
      if (path.startsWith("/api/review?status=pending")) return Promise.resolve([...pending]);
      const single = path.match(/^\/api\/review\/(\d+)\/approve$/);
      if (single && init?.method === "POST")
        return new Promise((resolve) => verdicts.set(Number(single[1]), () => resolve({})));
      if (path === "/api/review/approve-batch") {
        const ids = JSON.parse(String(init?.body)).ids as number[];
        batches.push(ids);
        return Promise.resolve({ results: ids.map((id) => ({ id, status: "approved" })) });
      }
      return Promise.resolve([]);
    },
  };
});
vi.mock("next/navigation", () => ({ usePathname: () => "/review" }));

import ReviewPage from "@/app/review/page";

beforeEach(() => {
  pending = [row(1), row(2), row(3)];
  verdicts.clear();
  batches.length = 0;
});

describe("overlapping review verdicts", () => {
  it("keeps every judged card out of the queue", async () => {
    render(<ReviewPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Approve proposal #1: add task 1" }));
    fireEvent.click(screen.getByRole("button", { name: "Approve proposal #2: add task 2" }));
    await act(async () => verdicts.get(1)!());
    await act(async () => verdicts.get(2)!());

    expect(screen.queryByRole("button", { name: "Approve proposal #1: add task 1" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Approve proposal #2: add task 2" })).toBeNull();
    expect(screen.getByRole("button", { name: "Approve proposal #3: add task 3" })).toBeTruthy();
  });

  it("drops a card judged on its own from the batch selection", async () => {
    render(<ReviewPage />);
    fireEvent.click(await screen.findByLabelText("Select #1 add task 1 for batch approval"));
    fireEvent.click(screen.getByLabelText("Select #3 add task 3 for batch approval"));
    fireEvent.click(screen.getByRole("button", { name: "Approve proposal #1: add task 1" }));
    await act(async () => verdicts.get(1)!());

    expect(screen.getByText("1 selected")).toBeTruthy();
    const changed = vi.fn();
    window.addEventListener("skein-attention-change", changed);
    fireEvent.click(screen.getByRole("button", { name: "Approve selected" }));
    await waitFor(() => expect(batches).toEqual([[3]]));
    await waitFor(() => expect(changed).toHaveBeenCalled());
    window.removeEventListener("skein-attention-change", changed);
  });
});
