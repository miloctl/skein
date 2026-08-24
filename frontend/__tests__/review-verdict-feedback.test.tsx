import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const row = {
  id: 1,
  entity: "task",
  entity_id: null,
  action: "create",
  payload: { title: "Ship it" },
  summary: "create a task",
  proposed_by: "scout",
  requested_by: null,
  origin: "agent",
  created_at: "2026-08-09T09:00:00+00:00",
  label: "add a task",
};
let pending: typeof row[] = [];

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    getUser: () => "tester",
    subscribeUser: () => () => {},
    api: (path: string, init?: RequestInit) => {
      if (path.startsWith("/api/review?status=pending")) return Promise.resolve([...pending]);
      if (path === "/api/review/1/reject" && init?.method === "POST") {
        pending = [];
        return Promise.resolve({});
      }
      return Promise.resolve([]);
    },
  };
});
vi.mock("next/navigation", () => ({ usePathname: () => "/review" }));

import ReviewPage from "@/app/review/page";
import { StatusRegion } from "@/components/status-region";

beforeEach(() => {
  pending = [row];
});

describe("review verdict feedback", () => {
  it("shows the reason limit, announces success, and restores focus", async () => {
    render(
      <>
        <ReviewPage />
        <StatusRegion />
      </>,
    );

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Reject proposal #1: add a task",
      }),
    );
    const reason = screen.getByLabelText(/Rejection reason/) as HTMLInputElement;
    expect(reason.maxLength).toBe(1000);
    expect(screen.getByText("Maximum 1,000 characters.")).toBeTruthy();
    expect(document.body.textContent).not.toContain("remaining");

    fireEvent.change(reason, { target: { value: "x".repeat(800) } });
    expect(screen.getByText(/200 remaining/)).toBeTruthy();

    fireEvent.change(reason, { target: { value: "The evidence is incomplete." } });
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));

    await waitFor(() =>
      expect(screen.getByRole("status").textContent).toContain("Proposal #1 rejected."),
    );
    const heading = screen.getByRole("heading", { level: 1, name: "Approvals" });
    await waitFor(() => expect(document.activeElement).toBe(heading));
  });
});
