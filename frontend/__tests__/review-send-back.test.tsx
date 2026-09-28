import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/** Rejecting a submitted task can send it back to its agent for one more
 *  turn. The choice is explicit and offered only where a wake can run: on a
 *  task_completion, never on another proposal. */

const completion = {
  id: 7,
  entity: "task_completion",
  entity_id: 4,
  action: "update",
  payload: {},
  summary: "mark task #4 done",
  proposed_by: "scout",
  requested_by: null,
  origin: "agent",
  created_at: "2026-08-20T09:00:00+00:00",
  label: "accept completed work",
  sponsor: "tester",
  evidence: {
    id: 4,
    title: "Draft the migration checklist",
    status: "in_progress",
    delegated_agent: "scout",
    acceptance_criteria: "",
    forge_url: null,
    worklog: [],
    sponsor_was: "",
    criteria_refs: [],
  },
};
const task = { ...completion, id: 8, entity: "task", entity_id: null, evidence: undefined };
let pending: Array<Record<string, unknown>> = [];
const posts: Array<{ path: string; body: unknown }> = [];

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    getUser: () => "tester",
    subscribeUser: () => () => {},
    api: (path: string, init?: RequestInit) => {
      if (path.startsWith("/api/review?status=pending")) return Promise.resolve([...pending]);
      if (init?.method === "POST") {
        const body = JSON.parse(String(init.body));
        posts.push({ path, body });
        pending = [];
        return Promise.resolve({ status: "rejected", sent_back: Boolean(body.send_back) });
      }
      return Promise.resolve([]);
    },
  };
});
vi.mock("next/navigation", () => ({ usePathname: () => "/review" }));

import ReviewPage from "@/app/review/page";
import { StatusRegion } from "@/components/status-region";

beforeEach(() => {
  posts.length = 0;
});

describe("reject and send back", () => {
  it("sends a task completion back only when the reviewer checks the box", async () => {
    pending = [completion];
    render(
      <>
        <ReviewPage />
        <StatusRegion />
      </>,
    );
    fireEvent.click(await screen.findByRole("button", { name: /Reject proposal #7/ }));
    const box = screen.getByRole("checkbox", {
      name: "Send the task back to scout for another turn",
    }) as HTMLInputElement;
    expect(box.checked).toBe(false);
    fireEvent.click(box);
    fireEvent.change(screen.getByLabelText(/Rejection reason/), {
      target: { value: "Target Postgres 17." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));

    await waitFor(() =>
      expect(screen.getByRole("status").textContent).toContain(
        "Proposal #7 rejected. The agent is queued for another turn.",
      ),
    );
    expect(posts.filter((p) => p.path.endsWith("/reject"))).toEqual([
      {
        path: "/api/review/7/reject",
        body: { note: "Target Postgres 17.", send_back: true },
      },
    ]);
  });

  it("offers no send-back on any other proposal", async () => {
    pending = [task];
    render(<ReviewPage />);
    fireEvent.click(await screen.findByRole("button", { name: /Reject proposal #8/ }));
    expect(screen.queryByRole("checkbox", { name: /Send the task back/ })).toBeNull();
  });
});
