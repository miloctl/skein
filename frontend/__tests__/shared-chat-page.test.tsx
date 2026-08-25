import { useEffect } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/components/chat-sidebar", () => ({
  ChatSidebar: ({ onOpen, threadKind }: { onOpen: (id: string, kind: "solo" | "shared") => void; threadKind: string }) => (
    <div>
      <span>kind:{threadKind}</span>
      <button onClick={() => onOpen("shared-room", "shared")}>Open shared room</button>
      <button onClick={() => onOpen("solo-room", "solo")}>Open solo room</button>
    </div>
  ),
}));
vi.mock("@/components/shared-chat", () => ({
  SharedChat: ({
    threadId,
    onTitle,
    onUnavailable,
  }: {
    threadId: string;
    onTitle: (title: string) => void;
    onUnavailable: () => void;
  }) => {
    useEffect(() => onTitle("Launch room"), [onTitle]);
    return (
      <div>
        Shared transcript {threadId}
        <button onClick={onUnavailable}>Lose shared access</button>
      </div>
    );
  },
}));
vi.mock("@/components/thread", () => ({ Thread: () => <div>Solo transcript</div> }));
vi.mock("@/components/thread-title", () => ({
  ThreadTitle: ({ threadId }: { threadId: string }) => <h1>Solo {threadId}</h1>,
}));
vi.mock("@/app/runtime-provider", () => ({
  RuntimeProvider: ({ children }: { children: React.ReactNode }) => children,
}));
vi.mock("next/navigation", () => ({ usePathname: () => "/chat" }));

import ChatPage from "@/app/chat/page";

beforeEach(() => {
  window.sessionStorage.clear();
  window.history.replaceState({}, "", "/chat");
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: () => ({
      matches: false,
      addEventListener: () => {},
      removeEventListener: () => {},
    }),
  });
});

describe("Chat page thread kinds", () => {
  it("switches between private shared and solo runtimes without mixing them", async () => {
    document.title = "Skein";
    render(<ChatPage />);
    expect(screen.getByText("Solo transcript")).toBeTruthy();
    expect(screen.getByText("kind:solo")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Open shared room" }));
    expect(screen.getByText("Shared transcript shared-room")).toBeTruthy();
    expect(screen.queryByText("Solo transcript")).toBeNull();
    expect(screen.getByText("kind:shared")).toBeTruthy();
    await waitFor(() => expect(document.title).toBe("Launch room — Skein"));
    expect(window.location.search).toBe("?shared=shared-room");
    expect(window.location.hash).toBe("");
    expect(JSON.parse(window.sessionStorage.getItem("skein-last-chat") ?? "{}")).toEqual({
      id: "shared-room",
      kind: "shared",
    });

    fireEvent.click(screen.getByRole("button", { name: "Open solo room" }));
    expect(screen.getByText("Solo transcript")).toBeTruthy();
    expect(screen.queryByText(/Shared transcript/)).toBeNull();
    await waitFor(() => expect(document.title).toBe("Chat — Skein"));
    expect(window.location.pathname).toBe("/chat");
    expect(window.location.search).toBe("");
    expect(window.location.hash).toBe("");
  });

  it("keeps a solo-chat composer prefill in the URL", () => {
    window.history.replaceState({}, "", "/chat?compose=%2Fhelp");
    render(<ChatPage />);

    expect(screen.getByText("Solo transcript")).toBeTruthy();
    expect(window.location.search).toBe("?compose=%2Fhelp");
  });

  it("opens a private shared chat from a notification deep link", async () => {
    window.history.replaceState(
      {},
      "",
      "/chat?shared=shared-room#shared-message-7",
    );
    render(<ChatPage />);

    expect(await screen.findByText("Shared transcript shared-room")).toBeTruthy();
    expect(screen.getByText("kind:shared")).toBeTruthy();
  });

  it("clears a revoked private room without claiming that a message failed", async () => {
    window.sessionStorage.setItem(
      "skein-last-chat",
      JSON.stringify({ id: "shared-room", kind: "shared" }),
    );
    render(<ChatPage />);
    expect(await screen.findByText("Shared transcript shared-room")).toBeTruthy();

    act(() => {
      fireEvent.click(screen.getByRole("button", { name: "Lose shared access" }));
    });

    expect(screen.getByRole("alert").textContent).toContain(
      "This private shared chat is no longer available. Select another chat.",
    );
    expect(screen.getByRole("alert").textContent).not.toContain("Message not sent");
    expect(window.sessionStorage.getItem("skein-last-chat")).toBeNull();
    await waitFor(() => expect(document.title).toBe("Private shared chat — Skein"));
    expect(screen.queryByText("Launch room")).toBeNull();
  });
});
