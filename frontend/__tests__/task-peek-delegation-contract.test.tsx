import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

/** The delegation contract's first slice: what done means and when to check
 *  in, written where the delegation is made. Both optional — a delegation
 *  without them is what every delegation was before — and the inputs appear
 *  only after an agent is picked, so they are not two mystery fields on
 *  every task panel. */

const sent: Record<string, unknown>[] = [];
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string, init?: { method?: string; body?: string }) => {
      if (path === "/api/agents")
        return Promise.resolve([{ agent: "scout", delegatable: true }]);
      if (path.endsWith("/delegate") && init?.method === "POST") {
        sent.push(JSON.parse(init.body as string));
        return Promise.resolve({});
      }
      if (path.endsWith("/worklog")) return Promise.resolve([]);
      return Promise.resolve({
        id: 4,
        title: "Build the happy path",
        status: "todo",
        priority: "high",
      });
    },
  };
});

import { TaskPeek } from "@/components/task-peek";

describe("the delegation contract fields", () => {
  it("appear once an agent is picked and travel with the delegation", async () => {
    window.history.pushState({}, "", "?task=4");
    render(<TaskPeek />);
    await waitFor(() =>
      expect(screen.getByRole("option", { name: "scout" })).toBeTruthy(),
    );
    // hidden until an agent is picked
    expect(screen.queryByLabelText("What done means")).toBeNull();

    fireEvent.change(screen.getByLabelText("Delegate to"), {
      target: { value: "scout" },
    });
    fireEvent.change(screen.getByLabelText("What done means"), {
      target: { value: "a runnable repro script" },
    });
    fireEvent.change(screen.getByLabelText("Check-in date"), {
      target: { value: "2026-09-01" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Delegate" }));

    await waitFor(() => expect(sent.length).toBe(1));
    expect(sent[0]).toEqual({
      agent: "scout",
      acceptance_criteria: "a runnable repro script",
      check_in_at: "2026-09-01",
    });
  });
});
