import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

/** The stored kind is `commitment` (API contract); the word a reader sees is
 *  `promise` (docs/LEXICON.md row 1). The mapping lives in KIND_LABEL and is
 *  applied at render time, so the source-text sweep in one-wording.test.ts
 *  cannot catch a revert — only a rendered assertion can. */

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    // the API answers with the WIRE kind — the palette must translate it
    api: async () => ({ kind: "commitment", id: 7 }),
  };
});

import { CapturePalette } from "@/components/capture-palette";

describe("quick capture and the promise label", () => {
  it("previews and confirms as promise, never commitment", async () => {
    render(<CapturePalette />);
    act(() => {
      window.dispatchEvent(new Event("skein-capture-open"));
    });

    const input = screen.getByLabelText("What to capture");
    fireEvent.change(input, { target: { value: "promised: ship the beta" } });
    expect(screen.getByText("will file as: promise")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Capture" }));
    expect(await screen.findByText("Captured as promise #7")).toBeTruthy();
    expect(screen.queryByText(/commitment/)).toBeNull();
  });
});
