import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

/** A bare C opens quick capture from any page, so a thought gets written down
 *  before it goes. It must never fire where C is a letter someone is typing,
 *  never as Ctrl+C, and never after the person turned it off: WCAG 2.1.4
 *  requires a way to turn off or remap a single-character shortcut. */

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    getUser: () => "ava",
    api: async () => ({ kind: "note", id: 1 }),
  };
});

import { CapturePalette } from "@/components/capture-palette";
import { setCaptureKeyEnabled } from "@/lib/capture-key";

const dialog = () => screen.queryByLabelText("What to capture");

afterEach(() => {
  setCaptureKeyEnabled(true);
  window.localStorage.clear();
});

describe("the C key", () => {
  it("opens quick capture when no text field has focus", () => {
    render(<CapturePalette />);
    act(() => {
      fireEvent.keyDown(document.body, { key: "c" });
    });
    expect(dialog()).toBeTruthy();
  });

  it("stays a letter inside a text field", () => {
    render(
      <>
        <input aria-label="Somewhere else" />
        <CapturePalette />
      </>,
    );
    const input = screen.getByLabelText("Somewhere else");
    input.focus();
    act(() => {
      fireEvent.keyDown(input, { key: "c" });
    });
    expect(dialog()).toBeNull();
  });

  it("leaves Ctrl+C and ⌘C to copy", () => {
    render(<CapturePalette />);
    act(() => {
      fireEvent.keyDown(document.body, { key: "c", ctrlKey: true });
      fireEvent.keyDown(document.body, { key: "c", metaKey: true });
    });
    expect(dialog()).toBeNull();
  });

  it("does nothing after the person turns it off", () => {
    setCaptureKeyEnabled(false);
    render(<CapturePalette />);
    act(() => {
      fireEvent.keyDown(document.body, { key: "c" });
    });
    expect(dialog()).toBeNull();
    expect(window.localStorage.getItem("skein-capture-key")).toBe("off");
  });
});
