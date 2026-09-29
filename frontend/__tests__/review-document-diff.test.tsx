import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

/** A document edit's payload is a quote and its replacement, so the card
 *  showed "—" beside the new text. It now shows the change as lines against
 *  the revision the proposal was filed on, and warns when the head moved. */

const row = {
  id: 31,
  entity: "document_edit",
  entity_id: 12,
  action: "update",
  payload: { old: "beta", new: "delta", base_revision: 1 },
  summary: "",
  proposed_by: "scribe",
  requested_by: null,
  origin: "agent",
  created_at: "2026-09-29T09:00:00+00:00",
  label: "change a document",
};

const state = vi.hoisted(() => ({ head: 1, settles: false, settled: false }));

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    getUser: () => "mira",
    subscribeUser: () => () => {},
    api: (path: string, init?: RequestInit) => {
      if (path === "/api/review/31/approve" && init?.method === "POST") {
        // review.approve_change settles a stale document edit as rejected
        state.settled = state.settles;
        return Promise.reject(
          state.settles
            ? new real.ApiError("could not apply and auto-rejected: document #12 changed after revision 1, and revision 2 is newer. Read revision 2, then make the change again.", 400)
            : new real.ApiError("the database is busy. Try again in a few seconds.", 503),
        );
      }
      if (path.startsWith("/api/review?status=pending"))
        return Promise.resolve(state.settled ? [] : [row]);
      if (path === "/api/review/31/diff")
        return Promise.resolve({
          id: 31,
          diff: {
            current: {},
            proposed: {},
            unified: "--- revision 1\n+++ proposed\n@@ -1,3 +1,3 @@\n alpha\n-beta\n+delta\n gamma",
            base_revision: 1,
            head_revision: state.head,
          },
        });
      return Promise.resolve([]);
    },
  };
});
vi.mock("next/navigation", () => ({ usePathname: () => "/review" }));

import ReviewPage from "@/app/review/page";

describe("a document edit in Approvals", () => {
  it("shows the change as lines against its base", async () => {
    state.head = 1;
    render(<ReviewPage />);
    expect(await screen.findByText("-beta")).toBeTruthy();
    expect(screen.getByText("+delta")).toBeTruthy();
    expect(screen.queryByText(/The document changed after this proposal/)).toBeNull();
  });

  it("warns that approval rejects it once the document moved", async () => {
    state.head = 2;
    render(<ReviewPage />);
    expect(
      await screen.findByText("The document changed after this proposal. If you approve it, Skein rejects it."),
    ).toBeTruthy();
  });

  it.each([
    [true, 0],
    [false, 1],
  ])("an approval that settles the proposal takes its card out (settles: %s)", async (settles, left) => {
    state.head = 2;
    state.settles = settles;
    state.settled = false;
    render(<ReviewPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Approve proposal #31: change a document" }));
    await waitFor(() => expect(screen.queryAllByText("+delta")).toHaveLength(left));
    // the settle check has run and left a card that is still pending
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.queryAllByText("+delta")).toHaveLength(left);
  });
});
