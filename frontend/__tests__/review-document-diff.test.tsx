import { render, screen } from "@testing-library/react";
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

const state = vi.hoisted(() => ({ head: 1 }));

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    getUser: () => "mira",
    subscribeUser: () => () => {},
    api: (path: string) => {
      if (path.startsWith("/api/review?status=pending")) return Promise.resolve([row]);
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

  it("warns that Approve refuses it once the document moved", async () => {
    state.head = 2;
    render(<ReviewPage />);
    expect(
      await screen.findByText("The document changed after this proposal. Approve refuses it."),
    ).toBeTruthy();
  });
});
