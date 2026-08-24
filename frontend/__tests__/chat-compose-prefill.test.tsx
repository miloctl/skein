import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

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
    api: (path: string) => {
      if (path === "/api/personas")
        return Promise.resolve([
          {
            slug: "bosun",
            name: "Bosun",
            description: "product guidance",
            emoji: "🪢",
          },
        ]);
      if (path === "/api/agents/status")
        return Promise.resolve({ provider: "ollama" });
      return Promise.resolve([]);
    },
    getUser: () => "tester",
  };
});

import { AssistantRuntimeProvider, useLocalRuntime } from "@assistant-ui/react";

import { Thread } from "@/components/thread";
import { dismissStatus, getStatus } from "@/lib/status";

function Harness() {
  const runtime = useLocalRuntime({
    async run() {
      return { content: [{ type: "text" as const, text: "ok" }] };
    },
  });
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <Thread />
    </AssistantRuntimeProvider>
  );
}

beforeEach(() => {
  window.history.replaceState(null, "", "/chat");
  dismissStatus();
});

describe("chat composer handoff", () => {
  it("prefills visible text once and consumes the URL parameter", async () => {
    const compose = "/as bosun I am on the /review page. ";
    window.history.replaceState(
      null,
      "",
      `/chat?compose=${encodeURIComponent(compose)}`,
    );

    const ready: string[] = [];
    const onReady = (event: Event) => ready.push((event as CustomEvent<string>).detail);
    window.addEventListener("skein-chat-compose-ready", onReady);
    render(<Harness />);

    const input = screen.getByRole("combobox") as HTMLTextAreaElement;
    await waitFor(() => expect(input.value).toBe(compose));
    expect(document.activeElement).toBe(input);
    expect(ready).toEqual([compose]);
    expect(window.location.search).toBe("");
    window.removeEventListener("skein-chat-compose-ready", onReady);
  });

  it("reports readiness when the same text is already in the mounted composer", async () => {
    render(<Harness />);
    const input = screen.getByRole("combobox") as HTMLTextAreaElement;
    fireEvent.change(input, { target: { value: "/help" } });
    await waitFor(() => expect(input.value).toBe("/help"));
    const ready: string[] = [];
    const onReady = (event: Event) => ready.push((event as CustomEvent<string>).detail);
    window.addEventListener("skein-chat-compose-ready", onReady);

    act(() => {
      window.dispatchEvent(new CustomEvent("skein-chat-compose", { detail: "/help" }));
    });

    await waitFor(() => expect(ready).toEqual(["/help"]));
    expect(document.activeElement).toBe(input);
    window.removeEventListener("skein-chat-compose-ready", onReady);
  });

  it("removes an oversized prefill without putting it in the composer", async () => {
    window.history.replaceState(
      null,
      "",
      `/chat?compose=${encodeURIComponent("x".repeat(501))}`,
    );

    render(<Harness />);

    const input = screen.getByRole("combobox") as HTMLTextAreaElement;
    await waitFor(() => expect(window.location.search).toBe(""));
    expect(input.value).toBe("");
    expect(getStatus()?.message).toBe(
      "The chat prefill is too long. Shorten it to 500 characters or fewer.",
    );
  });
});
