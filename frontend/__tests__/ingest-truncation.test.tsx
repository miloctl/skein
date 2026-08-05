import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

/** The "Not captured" heading counts every unclassified line while the list
 *  renders at most 20. Silent truncation under an honest count is worse than
 *  either alone: the reader counts twenty rows beneath a heading that says
 *  thirty-five and cannot tell which number to believe. */

const UNCLASSIFIED = Array.from({ length: 35 }, (_, i) => `musing number ${i}`);

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: () => Promise.resolve({ proposals: [], unclassified: UNCLASSIFIED, skipped_private: 0 }),
    getUser: () => "tester",
  };
});
vi.mock("next/navigation", () => ({ usePathname: () => "/ingest" }));

import IngestPage from "@/app/ingest/page";

describe("pasted notes with more unclassified lines than the list shows", () => {
  it("says how many are missing instead of dropping them silently", async () => {
    render(<IngestPage />);
    fireEvent.change(screen.getByLabelText(/Paste your notes/i), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: /Extract proposals/i }));

    // the honest total stays in the heading
    expect(await screen.findByText(/Not captured \(35\)/)).toBeTruthy();
    // and the gap is named rather than left for the reader to count
    expect(screen.getByText(/15 more lines are not shown/)).toBeTruthy();
  });
});
