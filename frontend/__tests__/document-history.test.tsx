import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/** History names who made each revision, and a restore sends the head the
 *  list showed, so a restore over somebody's newer save is a 409, never a
 *  silent overwrite. */

const state = vi.hoisted(() => ({ posts: [] as Array<{ path: string; body: unknown }>, reportStatus: vi.fn() }));

vi.mock("@/lib/status", () => ({ reportStatus: state.reportStatus }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        state.posts.push({ path, body: JSON.parse(String(init.body)) });
        return Promise.resolve({ id: 12, revision: 4, restored_from: 1, unchanged: false });
      }
      return Promise.resolve({
        id: 12,
        head: 3,
        revisions: [
          { revision: 3, author: "scribe", origin: "agent_verified", change_id: 31, restored_from: null, created_at: "2026-09-29T10:00:00+00:00" },
          { revision: 2, author: "raj", origin: "human", change_id: null, restored_from: null, created_at: "2026-09-29T09:00:00+00:00" },
          { revision: 1, author: "scribe", origin: "agent", change_id: null, restored_from: null, created_at: "2026-09-29T08:00:00+00:00" },
        ],
      });
    },
  };
});

import { DocumentHistory } from "@/components/document-history";

beforeEach(() => {
  state.posts = [];
  state.reportStatus.mockReset();
});

describe("document history", () => {
  it("names the author of each revision", async () => {
    render(<DocumentHistory artifactId={12} onRestored={() => {}} />);
    const items = await screen.findAllByRole("listitem");
    expect(items.map((li) => li.textContent)).toEqual([
      expect.stringContaining("scribe · Agent, approved in proposal #31"),
      expect.stringContaining("raj · Person"),
      expect.stringContaining("scribe · Agent"),
    ]);
    expect(screen.getByRole("link", { name: "proposal #31" }).getAttribute("href")).toBe(
      "/review?id=31",
    );
  });

  it("restore sends the head the list showed", async () => {
    const onRestored = vi.fn();
    render(<DocumentHistory artifactId={12} onRestored={onRestored} />);
    fireEvent.click(await screen.findByRole("button", { name: "Restore revision 1" }));
    await waitFor(() => expect(onRestored).toHaveBeenCalled());
    expect(state.posts).toEqual([
      { path: "/api/documents/12/revisions/1/restore", body: { base_revision: 3 } },
    ]);
    expect(state.reportStatus).toHaveBeenCalledWith(
      "Restored revision 1 as revision 4.",
      "confirmation",
    );
    expect(screen.queryByRole("button", { name: "Restore revision 3" })).toBeNull();
  });
});
