import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

/** The reviewer judged every proposal blind. Approval rate and streak were
 *  computed already and rendered on Team → Agents — two pages from the one
 *  screen where the number decides something, so a reviewer approving a
 *  fourth proposal in a row could not see that it was the fourth. */

const base = {
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

const rows: Record<string, unknown>[] = [];

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string) =>
      path.startsWith("/api/review?status=pending")
        ? Promise.resolve(rows)
        : Promise.resolve([]),
  };
});

vi.mock("next/navigation", () => ({ usePathname: () => "/review" }));

import ReviewPage from "@/app/review/page";

const load = (record: unknown) => {
  rows.length = 0;
  rows.push({ ...base, record });
};

describe("the proposer's record on the approvals row", () => {
  it("puts the ask before the trust arithmetic", async () => {
    load({
      approved: 0,
      proposed: 1,
      approval_rate: 0,
      streak: 0,
      streak_blocked: "",
      level: "review",
      promotes_at: 0,
    });
    render(<ReviewPage />);
    const summary = await screen.findByText("create a task");
    const record = screen.getByText(/settled proposal to add a task/);
    // the card led with the record, so a reviewer met the streak maths and
    // the identity lecture before the sentence saying what the agent wants —
    // the summary must come first in document order, the record beside the
    // verdict buttons
    expect(
      summary.compareDocumentPosition(record) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("states the counts and the run of approvals", async () => {
    load({
      approved: 12,
      proposed: 13,
      approval_rate: 0.92,
      streak: 4,
      streak_blocked: "",
      level: "review",
      promotes_at: 0,
    });
    render(<ReviewPage />);
    expect(await screen.findByText(/12 of 13/)).toBeTruthy();
    // "settled", so the count cannot be read as 13 proposals MADE
    // the lexicon LABEL, not the raw entity slug
    expect(screen.getByText(/settled proposals to add a task approved/)).toBeTruthy();
    expect(screen.getByText(/92%/)).toBeTruthy();
    expect(screen.getByText(/4 approvals in a row/)).toBeTruthy();
    expect(screen.queryByText(/suggests a promotion/)).toBeNull();
  });

  it("says so when THIS verdict is the one that earns a promotion", async () => {
    load({
      approved: 4,
      proposed: 4,
      approval_rate: 1,
      streak: 4,
      streak_blocked: "",
      level: "review",
      promotes_at: 5,
    });
    render(<ReviewPage />);
    expect(await screen.findByText(/One more approval makes 5 in a row/)).toBeTruthy();
  });

  it("claims no history for a proposer that has none", async () => {
    // null, not a zeroed record: "0 of 0 approved" is a claim about a history
    // that does not exist
    load(null);
    render(<ReviewPage />);
    expect(await screen.findByText(/create a task/)).toBeTruthy();
    expect(screen.queryByText(/approved here/)).toBeNull();
    expect(screen.queryByText(/0 of 0/)).toBeNull();
  });

  it("reads a zero streak as words, never as a bare 0", async () => {
    load({
      approved: 1,
      proposed: 4,
      approval_rate: 0.25,
      streak: 0,
      streak_blocked: "",
      level: "review",
      promotes_at: 0,
    });
    render(<ReviewPage />);
    expect(await screen.findByText(/last settled verdict was not an approval/)).toBeTruthy();
  });

  it("computes agreement on the proposal count at one", async () => {
    load({
      approved: 1,
      proposed: 1,
      approval_rate: 1,
      streak: 1,
      streak_blocked: "",
      level: "review",
      promotes_at: 0,
    });
    render(<ReviewPage />);
    // singular: "1 settled proposals to add a task" is the plural bug
    expect(await screen.findByText(/settled proposal to add a task approved/)).toBeTruthy();
  });

  it("computes agreement at a streak of one", async () => {
    load({
      approved: 1,
      proposed: 1,
      approval_rate: 1,
      streak: 1,
      streak_blocked: "",
      level: "review",
      promotes_at: 0,
    });
    render(<ReviewPage />);
    expect(await screen.findByText(/1 approval in a row/)).toBeTruthy();
  });

  /** In trusted-header mode — the DEFAULT — every verdict is weak, so the
   *  streak is 0 for everyone. A bare "no approvals in a row" beside "8 of 8
   *  approved" states a perfect record and no run in the same breath, and the
   *  promotion line could never appear. Say why instead. */
  it("withholds the run when no streak can form, and says why", async () => {
    load({
      approved: 8,
      proposed: 8,
      approval_rate: 1,
      streak: 0,
      streak_blocked:
        "Skein recorded 8 verdicts. None used strong identity. Only strong-identity verdicts count toward a promotion streak. If deployment sign-in is available, use it before you approve or reject. Otherwise, use a personal API key.",
      level: "review",
      promotes_at: 0,
    });
    render(<ReviewPage />);
    expect(await screen.findByText(/None used strong identity/)).toBeTruthy();
    expect(screen.queryByText(/in a row/)).toBeNull();
    expect(screen.queryByText(/was not an approval/)).toBeNull();
  });
});
