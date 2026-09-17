import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

/** A provider that accepts the request and never answers used to hold the
 *  composer for the whole run: Send is disabled while a turn runs, and no
 *  control ended the run. Stop is that control. The real runtime is used so
 *  the button's effect on isRunning is the library's, not a mock's. */

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
    // never resolves unless aborted: the hung-provider case
    run: ({ abortSignal }) =>
      new Promise((_resolve, reject) => {
        // a plain Error named AbortError: the runtime files it as a cancel
        // and swallows it; a jsdom DOMException is not an Error there
        abortSignal.addEventListener("abort", () =>
          reject(Object.assign(new Error("stopped"), { name: "AbortError" })),
        );
      }),
  });
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <Thread />
    </AssistantRuntimeProvider>
  );
}

const composer = () => screen.getByRole("textbox", { name: /Message/ }) as HTMLTextAreaElement;

describe("a turn that never answers", () => {
  it("offers Stop instead of Send, and Stop frees the composer", async () => {
    render(<Harness />);
    fireEvent.change(composer(), { target: { value: "are you there" } });
    fireEvent.keyDown(composer(), { key: "Enter" });
    const stop = await screen.findByRole("button", { name: "Stop" });
    expect(screen.queryByRole("button", { name: "Send" })).toBeNull();
    fireEvent.click(stop);
    await screen.findByRole("button", { name: "Send" });
    await waitFor(() => expect(screen.queryByRole("button", { name: "Stop" })).toBeNull());
    // the next message can go
    fireEvent.change(composer(), { target: { value: "again" } });
    expect((screen.getByRole("button", { name: "Send" }) as HTMLButtonElement).disabled).toBe(false);
  });
});

function RefusedHarness() {
  const runtime = useLocalRuntime({
    // the backend refused before the first token; the sentence rides the
    // thrown Error, which the runtime stores as a plain {code, message}
    run: () =>
      Promise.reject(new Error("The model session is in use. Wait for the current turn to finish.")),
  });
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <Thread />
    </AssistantRuntimeProvider>
  );
}

describe("a turn the server refused", () => {
  it("shows the server's sentence, not a bare ending", async () => {
    render(<RefusedHarness />);
    fireEvent.change(composer(), { target: { value: "again" } });
    fireEvent.keyDown(composer(), { key: "Enter" });
    expect(
      (await screen.findByText("The model session is in use. Wait for the current turn to finish."))
        .getAttribute("role"),
    ).toBe("status");
    expect(screen.queryByText("The turn ended without a reply.")).toBeNull();
  });
});
