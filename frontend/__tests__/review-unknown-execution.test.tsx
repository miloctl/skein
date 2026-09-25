import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const proposal = {
  id: 41,
  entity: "extension_core_tool",
  entity_id: null,
  action: "create",
  payload: { tool: "create_task" },
  summary: "run a governed stock tool",
  proposed_by: "scout",
  requested_by: "mira",
  origin: "agent",
  created_at: "2026-08-22T09:00:00+00:00",
  label: "run a governed stock tool",
};
let pending = [proposal];
let history: Record<string, unknown>[] = [];
let outcome = "completion_unknown";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    getUser: () => "mira",
    subscribeUser: () => () => {},
    api: (path: string) => {
      if (path.startsWith("/api/review?status=pending"))
        return Promise.resolve([...pending]);
      if (path.startsWith("/api/review?status=approved"))
        return Promise.resolve([...history]);
      if (path === "/api/review/41/approve") {
        pending = [];
        history = [
          {
            ...proposal,
            status: "approved",
            reviewed_by: "mira",
            reviewed_override: 0,
            execution_status: outcome,
          },
        ];
        return Promise.resolve({
          status: "approved",
          execution_status: outcome,
        });
      }
      return Promise.resolve([]);
    },
  };
});
vi.mock("next/navigation", () => ({ usePathname: () => "/review" }));

import ReviewPage from "@/app/review/page";
import { StatusRegion } from "@/components/status-region";

beforeEach(() => {
  pending = [proposal];
  history = [];
  outcome = "completion_unknown";
});

describe("reviewed execution with unknown completion", () => {
  it("settles the verdict, warns against retry, and keeps the outcome in history", async () => {
    render(
      <>
        <ReviewPage />
        <StatusRegion />
      </>,
    );
    fireEvent.click(
      await screen.findByRole("button", {
        name: "Approve proposal #41: run a governed stock tool",
      }),
    );
    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toContain(
        "remote completion is unknown. Do not retry",
      ),
    );
    await screen.findByRole("heading", { name: "Execution needs reconciliation" });
    expect(document.body.textContent).toContain(
      "Completion is unknown. Check the external system. Do not retry this action.",
    );
    expect(document.body.textContent).toContain("Approved by mira");
    expect(screen.queryByText("run a governed stock tool", { selector: "h2" })).toBeNull();
  });

  it("says a failed remote call changed nothing", async () => {
    outcome = "failed";
    render(
      <>
        <ReviewPage />
        <StatusRegion />
      </>,
    );
    fireEvent.click(
      await screen.findByRole("button", {
        name: "Approve proposal #41: run a governed stock tool",
      }),
    );
    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toContain(
        "Proposal #41 was approved, but the remote call failed. Nothing changed.",
      ),
    );
    await waitFor(() =>
      expect(document.body.textContent).toContain(
        "The remote call failed. Nothing changed. #41",
      ),
    );
    expect(document.body.textContent).not.toContain("✅ #41");
  });
});
