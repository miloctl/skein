import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

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
    if (path === "/api/tasks/browse") return Promise.resolve({ open: [], done: [] });
    if (path === "/api/absences")
      return Promise.resolve([
        {
          id: 7,
          person: "Ava",
          kind: "PTO",
          starts_on: "2026-08-20",
          ends_on: "2026-08-22",
          note: "beach",
          visibility: "private",
          crew_id: 0,
        },
      ]);
    if (path === "/api/notes")
      return Promise.resolve([
        {
          id: 9,
          topic: "Launch notes",
          content: "The launch sequence and owner map.",
          visibility: "private",
          crew_id: 0,
        },
      ]);
    if (path === "/api/pulse") return Promise.resolve(null);
    return Promise.resolve([]);
  });
});

describe("dashboard deletion confirmations", () => {
  it("states what time-away deletion changes before it sends the request", async () => {
    render(<Dashboard />);
    const trigger = await screen.findByRole("button", {
      name: "Delete Ava's PTO 2026-08-20",
    });
    fireEvent.click(trigger);

    const consequence = screen.getByText(
      /Current capacity and weekly planning will no longer use it.*An activity record of the deletion will stay.*Backups can retain the full entry/i,
    );
    const confirm = screen.getByRole("button", { name: "Delete time away" });
    expect(confirm.getAttribute("aria-describedby")).toBe(consequence.id);
    expect(
      mocks.api.mock.calls.filter(([, opts]) => opts?.method === "DELETE"),
    ).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "Cancel deletion" }));
    await waitFor(() =>
      expect(document.activeElement).toBe(
        screen.getByRole("button", { name: trigger.getAttribute("aria-label")! }),
      ),
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Delete Ava's PTO 2026-08-20" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Delete time away" }));
    await waitFor(() =>
      expect(mocks.api).toHaveBeenCalledWith("/api/absences/7", {
        method: "DELETE",
      }),
    );
  });

  it("states what note deletion removes and lets Escape cancel", async () => {
    render(<Dashboard />);
    const trigger = await screen.findByRole("button", {
      name: "Delete note: Launch notes",
    });
    fireEvent.click(trigger);

    const confirm = screen.getByRole("button", { name: "Delete note" });
    const consequence = screen.getByText(
      /It will leave the knowledge base and search.*activity record can retain up to 300 characters.*backups can retain the note/i,
    );
    expect(confirm.getAttribute("aria-describedby")).toBe(consequence.id);
    expect(
      mocks.api.mock.calls.filter(([, opts]) => opts?.method === "DELETE"),
    ).toHaveLength(0);

    fireEvent.keyDown(confirm, { key: "Escape" });
    await waitFor(() =>
      expect(document.activeElement).toBe(
        screen.getByRole("button", { name: trigger.getAttribute("aria-label")! }),
      ),
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Delete note: Launch notes" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Delete note" }));
    await waitFor(() =>
      expect(mocks.api).toHaveBeenCalledWith("/api/notes/9", {
        method: "DELETE",
      }),
    );
  });
});
