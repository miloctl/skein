import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

/** Notes pasted twice name the proposal that already waits instead of
 *  counting it as created (services/ingest.py, review.DuplicateProposal). */

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    getUser: () => "ava",
    api: async (path: string) => {
      if (path === "/api/ingest") {
        return {
          proposals: [
            { id: 7, kind: "task", line: "todo: x" },
            { id: 3, kind: "question", line: "q: why", already_pending: true },
          ],
          unclassified: [],
          skipped_private: 0,
        };
      }
      return [];
    },
  };
});
vi.mock("next/navigation", () => ({
  usePathname: () => "/ingest",
  useSearchParams: () => new URLSearchParams(""),
}));

import IngestPage from "@/app/ingest/page";

describe("a paste that repeats an earlier one", () => {
  it("counts only the new proposal and names the pending one", async () => {
    render(<IngestPage />);
    fireEvent.change(screen.getByLabelText(/Paste your notes/i), {
      target: { value: "todo: x\nq: why" },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Extract proposals/i }));
    });
    expect(await screen.findByText(/1 proposal created/)).toBeTruthy();
    expect(screen.getByText(/1 already pending/)).toBeTruthy();
    expect(screen.getByText("already pending as #3")).toBeTruthy();
  });
});
