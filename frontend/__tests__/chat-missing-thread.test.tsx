import { useState } from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/app/runtime-provider", () => ({
  RuntimeProvider: ({ threadId, children }: { threadId: string; children: React.ReactNode }) => {
    const [optimistic, setOptimistic] = useState("");
    return (
      <div data-testid="runtime" data-thread={threadId}>
        <button onClick={() => setOptimistic("unsent message")}>Simulate send</button>
        {optimistic}
        {children}
      </div>
    );
  },
}));
vi.mock("@/components/chat-sidebar", () => ({ ChatSidebar: () => null }));
vi.mock("@/components/thread-title", () => ({
  ThreadTitle: ({ threadId }: { threadId: string }) => <span>{threadId}</span>,
}));
vi.mock("@/components/thread", () => ({ Thread: () => <p>thread</p> }));
vi.mock("@/lib/chat-layout", () => ({
  getSidebarCollapsed: () => false,
  serverSidebarCollapsed: () => false,
  subscribeChatLayout: () => () => {},
  toggleSidebar: vi.fn(),
}));
vi.mock("@/lib/persona", () => ({ setActivePersona: vi.fn() }));

import ChatPage from "@/app/chat/page";

beforeEach(() => {
  sessionStorage.clear();
  sessionStorage.setItem("skein-last-chat", "stale-thread");
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }));
  vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue(
    "11111111-1111-4111-8111-111111111111",
  );
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("a chat that disappeared from the backend", () => {
  it("states that the message was not sent and offers a fresh chat", () => {
    render(<ChatPage />);
    fireEvent.click(screen.getByRole("button", { name: "Simulate send" }));
    expect(screen.getByText("unsent message")).toBeTruthy();

    act(() => {
      window.dispatchEvent(
        new CustomEvent("skein-chat-missing", {
          detail: { threadId: "stale-thread" },
        }),
      );
    });

    expect(screen.getByRole("alert").textContent).toContain("Message not sent");
    expect(screen.queryByText("unsent message")).toBeNull();
    expect(screen.queryByTestId("runtime")).toBeNull();
    expect(sessionStorage.getItem("skein-last-chat")).toBeNull();
    const recovery = screen.getByRole("button", { name: "New chat" });
    expect(document.activeElement).toBe(recovery);

    fireEvent.click(recovery);

    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByTestId("runtime").getAttribute("data-thread")).toBe(
      "11111111-1111-4111-8111-111111111111",
    );
  });
});
