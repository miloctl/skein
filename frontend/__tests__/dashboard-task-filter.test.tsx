import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/** Browse's open list measured 82 rows over a 5,400px page with no way to
 *  narrow it — finding "my in-progress work" meant scrolling. One needle
 *  over title, #id, @assignee, status and priority. */

const mocks = vi.hoisted(() => ({ api: vi.fn() }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: mocks.api };
});
vi.mock("next/navigation", () => ({ usePathname: () => "/dashboard" }));

import Dashboard from "@/app/dashboard/page";

const task = (id: number, title: string, assignee: string, status = "todo") => ({
  id, title, assignee, status, priority: "medium", visibility: "workspace", crew_id: 0,
});

beforeEach(() => {
  vi.clearAllMocks();
  mocks.api.mockImplementation((path: string, opts?: { method?: string }) => {
    if (opts?.method) return Promise.resolve({});
    if (path === "/api/tasks/browse")
      return Promise.resolve({
        open: [
          task(1, "Map the drop-off points", "ava", "in_progress"),
          task(2, "Rotate the credentials", "dana"),
          task(3, "Draft the comparison", "ava"),
        ],
        done: [],
      });
    if (path === "/api/pulse") return Promise.resolve(null);
    return Promise.resolve([]);
  });
});

describe("the Browse task filter", () => {
  it("narrows by assignee and status, and says when nothing matches", async () => {
    render(<Dashboard />);
    expect(await screen.findByText(/Map the drop-off points/)).toBeTruthy();

    const box = screen.getByLabelText("Filter tasks");
    fireEvent.change(box, { target: { value: "@ava" } });
    expect(screen.getByText(/Map the drop-off points/)).toBeTruthy();
    expect(screen.getByText(/Draft the comparison/)).toBeTruthy();
    expect(screen.queryByText(/Rotate the credentials/)).toBeNull();

    fireEvent.change(box, { target: { value: "in_progress" } });
    expect(screen.getByText(/Map the drop-off points/)).toBeTruthy();
    expect(screen.queryByText(/Draft the comparison/)).toBeNull();

    fireEvent.change(box, { target: { value: "zz-nothing" } });
    expect(screen.getByText("No open task matches the filter.")).toBeTruthy();
  });
});
