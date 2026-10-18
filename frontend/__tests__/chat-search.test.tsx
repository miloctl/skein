import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

class NoopResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal("ResizeObserver", NoopResizeObserver);

const mocks = vi.hoisted(() => ({ api: vi.fn(), reportStatus: vi.fn() }));
vi.mock("@/lib/api", async (original) => ({
  ...(await original<typeof import("@/lib/api")>()),
  api: mocks.api,
}));
vi.mock("@/lib/chat-threads", () => ({
  chatThreads: () =>
    Promise.resolve([
      { id: "listed", title: "Listed chat", folder: "", engagement_id: null, updated_at: "2026-10-01T00:00:00+00:00" },
      { id: "one", title: "One", folder: "", engagement_id: null, updated_at: "2026-10-01T00:00:00+00:00" },
    ]),
}));
vi.mock("@/lib/status", () => ({ reportStatus: mocks.reportStatus }));

import { RuntimeProvider } from "@/app/runtime-provider";
import { ChatSidebar } from "@/components/chat-sidebar";
import { Thread } from "@/components/thread";
import { ApiError } from "@/lib/api";

const found = {
  results: [
    {
      thread_id: "cert-chat",
      kind: "solo",
      title: "Staging certificates",
      message_id: 7,
      matched_at: "2026-10-16T09:00:00+00:00",
      snippet: "…<b>rotat</b>e it, then paste <img src=x onerror=alert(1)> here…",
    },
    {
      thread_id: "shared-room",
      kind: "shared",
      title: "Rotation room",
      message_id: null,
      matched_at: "2026-10-15T09:00:00+00:00",
      snippet: "",
    },
  ],
  truncated: false,
};
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => {
    resolve = yes;
  });
  return { promise, resolve };
}
const searchBox = () => screen.getByRole("searchbox", { name: "Search your chats" });
function search(q: string) {
  fireEvent.change(searchBox(), { target: { value: q } });
  fireEvent.submit(searchBox().closest("form")!);
}

describe("chat search in the sidebar", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.api.mockImplementation((path: string) =>
      path.startsWith("/api/chats/search") ? Promise.resolve(found) : Promise.resolve([]),
    );
  });

  it("replaces the list with hits, renders snippets as text, and opens a hit at its message", async () => {
    const onOpen = vi.fn();
    render(<ChatSidebar threadId="" onOpen={onOpen} onNew={() => {}} />);
    await screen.findByText("Listed chat");

    search("rotat");

    expect(await screen.findByText("2 chats match.")).toBeTruthy();
    expect(mocks.api).toHaveBeenCalledWith("/api/chats/search?q=rotat", { cache: "no-store" });
    expect(screen.queryByText("Listed chat")).toBeNull();
    expect(screen.getByText("rotat").tagName).toBe("MARK");
    expect(document.querySelector("img")).toBeNull();
    expect(screen.getByText(/paste <img src=x onerror=alert\(1\)> here/)).toBeTruthy();
    expect(screen.getByText("Shared")).toBeTruthy();
    expect(screen.queryByText(/More than 30/)).toBeNull();

    const activity = vi.fn();
    window.addEventListener("skein-chat-activity", activity);
    fireEvent.click(screen.getByRole("button", { name: /Staging certificates/ }));
    // the cached chat list is dropped first, or a chat made in another tab opens empty
    expect(activity).toHaveBeenCalledTimes(1);
    window.removeEventListener("skein-chat-activity", activity);
    fireEvent.click(screen.getByRole("button", { name: /Rotation room/ }));
    expect(onOpen.mock.calls).toEqual([
      ["cert-chat", "solo", 7],
      ["shared-room", "shared", undefined],
    ]);

    fireEvent.keyDown(searchBox(), { key: "Escape" });
    expect(await screen.findByText("Listed chat")).toBeTruthy();
    expect((searchBox() as HTMLInputElement).value).toBe("");
    expect(screen.queryByText("Staging certificates")).toBeNull();
  });

  it("says more than 30 chats match when the backend cut the list", async () => {
    // search_chats sends 30 results and truncated only when a 31st matched
    const thirty = Array.from({ length: 30 }, (_, n) => ({
      ...found.results[0],
      thread_id: `chat-${n}`,
      title: `Chat ${n}`,
    }));
    mocks.api.mockImplementation((path: string) =>
      path.startsWith("/api/chats/search")
        ? Promise.resolve({ results: thirty, truncated: true })
        : Promise.resolve([]),
    );
    render(<ChatSidebar threadId="" onOpen={() => {}} onNew={() => {}} />);
    await screen.findByText("Listed chat");

    search("rotat");

    // the advice is in the live status, so it is announced with the count
    expect((await screen.findByText(/^More than 30 chats match\./)).getAttribute("role")).toBe("status");
    expect(screen.getByRole("status").textContent).toBe(
      "More than 30 chats match. The list shows the 30 chats with the newest matches. To find fewer chats, add a word.",
    );
  });

  it("clears with Escape inside the phone drawer, and closes the drawer only on a second Escape", async () => {
    const onMobileClose = vi.fn();
    render(
      <ChatSidebar mobileOpen onMobileClose={onMobileClose} threadId="" onOpen={() => {}} onNew={() => {}} />,
    );
    await screen.findByText("Listed chat");
    search("rotat");
    await screen.findByText("2 chats match.");

    fireEvent.keyDown(searchBox(), { key: "Escape" });
    expect(await screen.findByText("Listed chat")).toBeTruthy();
    expect(onMobileClose).not.toHaveBeenCalled();

    fireEvent.keyDown(searchBox(), { key: "Escape" });
    expect(onMobileClose).toHaveBeenCalledTimes(1);
  });

  it("offers no New folder while results hide the folder list", async () => {
    render(<ChatSidebar threadId="" onOpen={() => {}} onNew={() => {}} />);
    await screen.findByText("Listed chat");
    search("rotat");
    await screen.findByText("2 chats match.");

    fireEvent.click(screen.getByRole("button", { name: "Chat list options" }));

    expect(screen.queryByRole("button", { name: /New folder/ })).toBeNull();
    expect(screen.getByRole("button", { name: /Select/ })).toBeTruthy();
  });

  it("says when nothing matches and when the search did not run", async () => {
    render(<ChatSidebar threadId="" onOpen={() => {}} onNew={() => {}} />);
    await screen.findByText("Listed chat");

    mocks.api.mockImplementation((path: string) =>
      path.startsWith("/api/chats/search")
        ? Promise.resolve({ results: [], truncated: false })
        : Promise.resolve([]),
    );
    search("nothing");
    expect(await screen.findByText("No chat matches these words.")).toBeTruthy();

    mocks.api.mockImplementation((path: string) =>
      path.startsWith("/api/chats/search")
        ? Promise.reject(new ApiError("The search has more than 8 words. Use 8 words or fewer.", 400))
        : Promise.resolve([]),
    );
    search("a b c d e f g h i");
    // the server's sentence stands alone
    expect((await screen.findByRole("alert")).textContent).toBe(
      "The search has more than 8 words. Use 8 words or fewer.",
    );

    // the same failure again remounts the alert, so it is announced again
    const first = screen.getByRole("alert");
    search("a b c d e f g h i");
    await waitFor(() => expect(screen.getByRole("alert")).not.toBe(first));

    mocks.api.mockImplementation((path: string) =>
      path.startsWith("/api/chats/search") ? Promise.reject(new TypeError("Failed to fetch")) : Promise.resolve([]),
    );
    search("rotat");
    expect((await screen.findByRole("alert")).textContent).toMatch(/^The search did not run\. /);
  });

  it("drops results and a running search when the identity changes", async () => {
    render(<ChatSidebar threadId="" onOpen={() => {}} onNew={() => {}} />);
    await screen.findByText("Listed chat");
    search("rotat");
    await screen.findByText("2 chats match.");

    // lib/theme.ts writes storage on every paint: not an identity change
    act(() => {
      window.dispatchEvent(new StorageEvent("storage", { key: "skein-theme" }));
    });
    expect(screen.getByText("Staging certificates")).toBeTruthy();

    window.dispatchEvent(new Event("skein-identity-change"));
    await screen.findByText("Listed chat");
    expect(screen.queryByText("Staging certificates")).toBeNull();

    const late = deferred<typeof found>();
    mocks.api.mockImplementation((path: string) =>
      path.startsWith("/api/chats/search") ? late.promise : Promise.resolve([]),
    );
    search("rotat");
    window.dispatchEvent(new Event("skein-identity-change"));
    await act(async () => late.resolve(found));
    expect(screen.queryByText("Staging certificates")).toBeNull();
    expect(screen.getByText("Listed chat")).toBeTruthy();
  });
});

describe("a solo chat opened at a search hit", () => {
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

  beforeEach(() => vi.clearAllMocks());

  it("loads through the target and lands focus on that message", async () => {
    mocks.api.mockImplementation((path: string) =>
      path.includes("/messages/page") ? Promise.resolve(page([7, 8, 9], 7)) : Promise.resolve([]),
    );
    render(
      <RuntimeProvider threadId="one" target={7}>
        <Thread />
      </RuntimeProvider>,
    );
    await screen.findByText("saved 9");

    expect(mocks.api).toHaveBeenCalledWith(
      "/api/chats/one/messages/page?through=7",
      expect.objectContaining({ cache: "no-store" }),
    );
    const hit = document.querySelector<HTMLElement>('[data-message-id="saved-7"]')!;
    await waitFor(() => expect(document.activeElement).toBe(hit));
    expect(hit.tabIndex).toBe(-1);
    expect(hit.classList.contains("focus:ring-2")).toBe(true);
    // focus lands on a message that says who wrote it
    expect(hit.getAttribute("role")).toBe("article");
    expect(hit.getAttribute("aria-label")).toBe("Your message");
    expect(document.querySelector('[data-message-id="saved-8"]')!.getAttribute("aria-label")).toBe("Reply");
  });

  it("pages back by itself to a target older than the loaded window, then lands", async () => {
    mocks.api.mockImplementation((path: string) =>
      path.includes("before=7")
        ? Promise.resolve(page([3, 4, 5, 6], null))
        : path.includes("/messages/page")
          ? Promise.resolve(page([7, 8, 9], 7))
          : Promise.resolve([]),
    );
    render(
      <RuntimeProvider threadId="one" target={3}>
        <Thread />
      </RuntimeProvider>,
    );

    await screen.findByText("saved 3");
    expect(mocks.api).toHaveBeenCalledWith(
      "/api/chats/one/messages/page?before=7",
      expect.objectContaining({ cache: "no-store" }),
    );
    const hit = document.querySelector<HTMLElement>('[data-message-id="saved-3"]');
    await waitFor(() => expect(document.activeElement).toBe(hit));
  });
});
