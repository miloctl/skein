import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

/** A bulk sweep is one action, not six rows. Six consecutive
 *  "deleted an attached file" entries taught the reader to skim the feed —
 *  and the feed is the product's honesty surface. Same-actor same-action
 *  runs fold into one expandable row; the Raw toggle keeps every row. */

const entry = (seq: number, over: Partial<Record<string, unknown>> = {}) => ({
  seq,
  actor: "emiliano",
  who: "you",
  sentence: "emiliano deleted an attached file",
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
    expect(await screen.findByText("— 3 rows")).toBeTruthy();
    expect(
      screen.getByText("research-agent deleted an attached file"),
    ).toBeTruthy();

    // expanding shows the stored text of every folded row
    fireEvent.click(screen.getByText("— 3 rows"));
    expect(screen.getByText(/#8 · .* artifact #8 \(1019072 bytes\)/)).toBeTruthy();
    expect(screen.getByText(/#6 · .* artifact #6 \(1019072 bytes\)/)).toBeTruthy();
  });

  it("humanizes byte counts in the sentence view and keeps them raw in Raw rows", async () => {
    render(<ActivityPage />);
    // the agent's single row renders its detail humanized (995 KB, not
    // "0.9 MB" — lib/size.ts picks the unit that says something)
    expect(await screen.findByText(/995 KB/)).toBeTruthy();

    fireEvent.click(screen.getByLabelText?.("Raw rows") ?? screen.getByText("Raw rows"));
    expect(screen.getAllByText(/1019072 bytes/).length).toBeGreaterThan(0);
  });
});
