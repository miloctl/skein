import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

/** An edit after a capture starts the next one. The previous result must not
 *  hide the "will file as" preview, and the success auto-close must not shut
 *  the dialog on text the reader is still typing. */

const mode = { fail: false };
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: async () => {
      if (mode.fail) throw new Error("Capture is refused. Check the text, then try again.");
      return { kind: "task", id: 7 };
    },
  };
});

import { CapturePalette } from "@/components/capture-palette";

afterEach(() => {
  mode.fail = false;
  vi.useRealTimers();
});

async function capture(text: string) {
  render(<CapturePalette />);
  act(() => {
    window.dispatchEvent(new Event("skein-capture-open"));
  });
  const input = screen.getByLabelText("What to capture");
  fireEvent.change(input, { target: { value: text } });
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Capture" }));
  });
  return input;
}

describe("the edit after a capture", () => {
  it("shows the preview again after a failure", async () => {
    mode.fail = true;
    const input = await capture("todo: ship the beta");
    expect(screen.getByText(/Capture is refused/)).toBeTruthy();

    fireEvent.change(input, { target: { value: "todo: ship the beta today" } });
    expect(screen.getByText("will file as: task")).toBeTruthy();
    expect(screen.queryByText(/Capture is refused/)).toBeNull();
  });

  it("keeps the dialog open while the reader types the next capture", async () => {
    vi.useFakeTimers();
    const input = await capture("todo: ship the beta");
    expect(screen.getByText("Captured as task #7")).toBeTruthy();

    fireEvent.change(input, { target: { value: "todo: write the notes" } });
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(screen.getByLabelText("What to capture")).toBeTruthy();
    fireEvent.keyDown(window, { key: "Escape" });
    fireEvent.keyDown(window, { key: "Escape" });
  });
});
