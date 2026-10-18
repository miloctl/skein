import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

class NoopResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal("ResizeObserver", NoopResizeObserver);

const mocks = vi.hoisted(() => ({ api: vi.fn() }));
vi.mock("@/lib/api", async (original) => ({
  ...(await original<typeof import("@/lib/api")>()),
  api: mocks.api,
}));
vi.mock("@/lib/chat-threads", () => ({
  chatThreads: () =>
    Promise.resolve([
      { id: "one", title: "One", folder: "", engagement_id: null, updated_at: "2026-10-01T00:00:00+00:00" },
    ]),
}));
// the sidebar stands in for chat-search.tsx: a hit is onOpen with a message id
vi.mock("@/components/chat-sidebar", () => ({
  ChatSidebar: ({ onOpen }: { onOpen: (id: string, kind: "solo", messageId: number) => void }) => (
    <div>
      <button onClick={() => onOpen("one", "solo", 8)}>hit eight</button>
      <button onClick={() => onOpen("one", "solo", 3)}>hit three</button>
    </div>
  ),
}));

import ChatPage from "@/app/chat/page";

const message = (id: number) => ({
  id,
  role: id % 2 ? "user" : "assistant",
  content: `saved ${id}`,
  created_at: "2026-09-01T12:00:00Z",
});
const page = (ids: number[], next_before: number | null) => ({
  messages: ids.map(message),
  next_before,
});

beforeEach(() => {
  vi.clearAllMocks();
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: () => ({ matches: false, addEventListener: () => {}, removeEventListener: () => {} }),
  });
  sessionStorage.setItem("skein-last-chat", JSON.stringify({ id: "one", kind: "solo" }));
  mocks.api.mockImplementation((path: string) =>
    path.includes("before=7")
      ? Promise.resolve(page([3, 4, 5, 6], null))
      : path.includes("/api/chats/one/messages/page")
        ? Promise.resolve(page([7, 8, 9], 7))
        : Promise.resolve([]),
  );
});

it("a hit in the chat on screen lands in place and keeps the draft and the transcript", async () => {
  render(<ChatPage />);
  await screen.findByText("saved 9");
  const newest = screen.getByText("saved 9");
  const box = screen.getByRole("textbox", { name: /Message/ });
  fireEvent.change(box, { target: { value: "unsent draft" } });

  fireEvent.click(screen.getByRole("button", { name: "hit eight" }));
  const eight = document.querySelector('[data-message-id="saved-8"]');
  await waitFor(() => expect(document.activeElement).toBe(eight));

  // older than the loaded window: paged back in place, never remounted
  fireEvent.click(screen.getByRole("button", { name: "hit three" }));
  await screen.findByText("saved 3");
  const three = document.querySelector('[data-message-id="saved-3"]');
  await waitFor(() => expect(document.activeElement).toBe(three));

  expect(screen.getByText("saved 9")).toBe(newest);
  expect((screen.getByRole("textbox", { name: /Message/ }) as HTMLTextAreaElement).value).toBe(
    "unsent draft",
  );
  expect(mocks.api.mock.calls.filter(([path]) => path.includes("/messages/page"))).toHaveLength(2);
});
