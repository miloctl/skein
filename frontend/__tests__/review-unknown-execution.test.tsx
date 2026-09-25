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
            entity: outcome === "failed" ? "extension_mcp_tool" : proposal.entity,
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
      if (path === "/api/review/approve-batch")
        return Promise.resolve({
          results: [
            { id: 41, status: "approved", execution_status: "completion_unknown" },
            { id: 42, status: "approved", execution_status: "failed" },
          ],
        });
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

  it("says a failed call did not complete, and never that nothing changed", async () => {
    // the backend records "failed" for extension and remote MCP tool calls
    // only (services/review.py::_extension_execution_status)
    pending = [{ ...proposal, entity: "extension_mcp_tool", label: proposal.label }];
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
        "Proposal #41 was approved, but the call did not complete.",
      ),
    );
    await waitFor(() =>
      expect(document.body.textContent).toContain(
        "The call did not complete. #41",
      ),
    );
    expect(document.body.textContent).not.toContain("✅ #41");
    expect(document.body.textContent).not.toContain("Nothing changed");
  });

  it("names every id of a mixed batch", async () => {
    pending = [
      proposal,
      { ...proposal, id: 42, entity: "extension_mcp_tool", summary: "read a page", label: "read a page" },
    ];
    render(
      <>
        <ReviewPage />
        <StatusRegion />
      </>,
    );
    fireEvent.click(
      await screen.findByLabelText("Select #41 run a governed stock tool for batch approval"),
    );
    fireEvent.click(screen.getByLabelText("Select #42 read a page for batch approval"));
    fireEvent.click(screen.getByRole("button", { name: "Approve selected" }));
    await waitFor(() => {
      const said = screen.getByRole("alert").textContent ?? "";
      expect(said).toContain("#41, but remote completion is unknown");
      expect(said).toContain("#42, but the call did not complete");
    });
  });
});
