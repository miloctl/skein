import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

/** The kind is `promise` end to end (docs/LEXICON.md row 1) — on the wire,
 *  in the preview, and in the confirmation. These strings are assembled at
 *  render time, so the source-text sweep in one-wording.test.ts cannot catch
 *  a revert — only a rendered assertion can. `promised:` and `commitment:`
 *  both stay accepted as typed input. */

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: async () => ({ kind: "promise", id: 7 }),
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
