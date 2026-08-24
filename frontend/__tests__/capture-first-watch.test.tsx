import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  fail: false,
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: () =>
      state.fail
        ? Promise.reject(new TypeError("Failed to fetch"))
        : Promise.resolve({ kind: "task", id: 42 }),
  };
});

import { CapturePalette } from "@/components/capture-palette";

beforeEach(() => {
  state.fail = false;
});

afterEach(() => vi.useRealTimers());

describe("First Watch capture contract", () => {
  it("prefills an empty draft and emits the task receipt after close and focus restoration", async () => {
    vi.useFakeTimers();
    const receipts: Array<{ kind: string; id: number }> = [];
    const onReceipt = (event: Event) =>
      receipts.push((event as CustomEvent<{ kind: string; id: number }>).detail);
    window.addEventListener("skein-capture-complete", onReceipt);
    render(
      <>
        <button type="button">First Watch capture</button>
        <CapturePalette />
      </>,
    );
    const opener = screen.getByRole("button", { name: "First Watch capture" });
    opener.focus();

    act(() => {
      window.dispatchEvent(
        new CustomEvent("skein-capture-open", { detail: { text: "todo: " } }),
      );
    });

    const input = screen.getByLabelText("What to capture") as HTMLTextAreaElement;
    expect(input.value).toBe("todo: ");
    expect(input.selectionStart).toBe("todo: ".length);
    expect(input.selectionEnd).toBe("todo: ".length);
    expect((screen.getByRole("button", { name: "Capture" }) as HTMLButtonElement).disabled).toBe(
      true,
    );

    fireEvent.change(input, { target: { value: "todo: Keep this task" } });
    fireEvent.click(screen.getByRole("button", { name: "Capture" }));
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.getByText("Captured as task #42")).toBeTruthy();
    expect(receipts).toEqual([]);

    await act(async () => {
      vi.advanceTimersByTime(1400);
      await Promise.resolve();
      vi.runOnlyPendingTimers();
    });

    expect(screen.queryByRole("dialog", { name: "Quick capture" })).toBeNull();
    expect(document.activeElement).toBe(opener);
    expect(receipts).toEqual([{ kind: "task", id: 42 }]);
    window.removeEventListener("skein-capture-complete", onReceipt);
  });

  it("closes an unchanged generated prefix in one Escape and restores focus", () => {
    render(
      <>
        <button type="button">First Watch capture</button>
        <CapturePalette />
      </>,
    );
    const opener = screen.getByRole("button", { name: "First Watch capture" });
    opener.focus();
    act(() => {
      window.dispatchEvent(
        new CustomEvent("skein-capture-open", { detail: { text: "todo: " } }),
      );
    });

    fireEvent.keyDown(window, { key: "Escape" });

    expect(screen.queryByRole("dialog", { name: "Quick capture" })).toBeNull();
    expect(document.activeElement).toBe(opener);
  });

  it("makes the background inert while Quick Capture is open", () => {
    const outside = document.createElement("button");
    outside.textContent = "Background control";
    document.body.appendChild(outside);
    render(<CapturePalette />);
    act(() => window.dispatchEvent(new Event("skein-capture-open")));

    expect(outside.hasAttribute("inert")).toBe(true);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(outside.hasAttribute("inert")).toBe(false);
    outside.remove();
  });

  it("does not submit the visible blocked-on chip without a body", () => {
    render(<CapturePalette />);
    act(() => {
      window.dispatchEvent(
        new CustomEvent("skein-capture-open", { detail: { text: "blocked on " } }),
      );
    });

    expect((screen.getByRole("button", { name: "Capture" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
  });

  it("keeps the draft and warns against a blind retry after a transport failure", async () => {
    state.fail = true;
    render(<CapturePalette />);
    act(() => {
      window.dispatchEvent(
        new CustomEvent("skein-capture-open", { detail: { text: "todo: " } }),
      );
    });
    const input = screen.getByLabelText("What to capture") as HTMLTextAreaElement;
    fireEvent.change(input, { target: { value: "todo: Keep this draft" } });

    fireEvent.click(screen.getByRole("button", { name: "Capture" }));

    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toContain(
        "Search for the record before you try again",
      ),
    );
    expect(input.value).toBe("todo: Keep this draft");
  });
});
