import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

/** A bulk sweep is one action, not six rows. Six consecutive
 *  "deleted an attached file" entries taught the reader to skim the feed —
 *  and the feed is the product's honesty surface. Same-actor same-action
 *  runs fold into one expandable row; the Raw toggle keeps every row. */

const entry = (seq: number, over: Partial<Record<string, unknown>> = {}) => ({
  seq,
  actor: "mira",
  who: "you",
  sentence: "mira deleted an attached file",
  salience: "normal",
  registered: true,
  action: "delete_file",
  detail: `artifact #${seq} (1019072 bytes)`,
  created_at: "2026-08-19T10:00:00+00:00",
  ...over,
});

const FEED = {
  entries: [
    entry(9, {
      actor: "scheduler",
      who: "system",
      action: "week_close",
      sentence: "scheduler closed the week",
      detail: "",
    }),
    entry(8),
    entry(7),
    entry(6),
    entry(5, {
      actor: "research-agent",
      who: "agent",
      sentence: "research-agent deleted an attached file",
    }),
    entry(4, {
      actor: "backend-architect",
      who: "agent",
      action: "propose_change",
      sentence: "backend-architect filed a proposal",
      detail: "#34 update task_completion",
    }),
  ],
  next_before: null,
};

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: () => Promise.resolve(FEED), getUser: () => "tester" };
});
vi.mock("next/navigation", () => ({ usePathname: () => "/activity" }));

import ActivityPage from "@/app/activity/page";

describe("activity feed grouping", () => {
  it("folds a same-actor same-action burst into one expandable row", async () => {
    render(<ActivityPage />);
    // the human's 3-row burst is one row; the agent's row must NOT fold into
    // it even though the action matches — different actor
    expect(await screen.findByText(/— 3 related actions/)).toBeTruthy();
    expect(
      screen.getByText("research-agent deleted an attached file"),
    ).toBeTruthy();

    // expanding keeps the readable sentence view; exact stored fields stay
    // behind Raw rows
    fireEvent.click(screen.getByText(/— 3 related actions/));
    expect(screen.getByText(/artifact #8 .*KB/)).toBeTruthy();
    expect(screen.getByText(/artifact #6 .*KB/)).toBeTruthy();
    expect(screen.queryByText(/#8 ·/)).toBeNull();
  });

  it("humanizes stored detail in the sentence view and keeps it raw in Raw rows", async () => {
    render(<ActivityPage />);
    // the agent's single row renders its byte count as 995 KB, not 0.9 MB.
    expect(await screen.findByText(/995 KB/)).toBeTruthy();
    expect(screen.getByText(/proposal #34 · update task completion/)).toBeTruthy();

    fireEvent.click(screen.getByLabelText?.("Raw rows") ?? screen.getByText("Raw rows"));
    expect(screen.getAllByText(/1019072 bytes/).length).toBeGreaterThan(0);
    expect(screen.getByText(/#34 update task_completion/)).toBeTruthy();
  });
});
