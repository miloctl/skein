import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

/** An IME conversion ends with Enter. With the slash popup open, that Enter
 *  ran the active row, so committing a Pinyin or Kana conversion inserted a
 *  command the person never chose. The library ignores a composing Enter
 *  since assistant-ui 0.15.24; the capture handler in thread.tsx runs first
 *  and must ignore it too. keyCode 229 is Safari's form of the same event. */

class NoopResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal("ResizeObserver", NoopResizeObserver);

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: () => Promise.reject(new Error("no backend in this test")),
    getUser: () => "tester",
  };
});

import { AssistantRuntimeProvider, useLocalRuntime } from "@assistant-ui/react";
import { Thread } from "@/components/thread";

function Harness() {
  const runtime = useLocalRuntime({
    async run() {
      return { content: [{ type: "text" as const, text: "noted" }] };
    },
  });
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <Thread />
    </AssistantRuntimeProvider>
  );
}

const composer = () => screen.getByRole("textbox", { name: /Message/ }) as HTMLTextAreaElement;

describe("Enter that ends an IME composition", () => {
  it.each([
    ["isComposing", { key: "Enter", isComposing: true }],
    ["keyCode 229", { key: "Enter", keyCode: 229 }],
  ])("leaves the popup and the draft alone (%s)", async (_label, init) => {
    render(<Harness />);
    const box = composer();
    fireEvent.change(box, { target: { value: "/b" } });
    // the popup must really be open, or this asserts nothing
    await screen.findByRole("listbox");
    fireEvent.keyDown(box, init);
    await waitFor(() => expect(box.value).toBe("/b"));
    expect(screen.queryByRole("listbox")).toBeTruthy();
  });
});
