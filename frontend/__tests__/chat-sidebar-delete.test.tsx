import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/** Bulk delete is the sidebar's only irreversible action, and it deletes one
 *  row per request. A failure part-way through therefore leaves the
 *  selection describing work that is already done: retrying it re-deletes
 *  ghosts and reports a 404 for a chat the person did delete. These pin the
 *  pruning that prevents it, and the active-thread reset that must fire only
 *  when the open chat was actually removed. */

const mocks = vi.hoisted(() => ({
  api: vi.fn(),
  reportStatus: vi.fn(),
  threads: vi.fn(),
}));

vi.mock("@/lib/chat-threads", () => ({ chatThreads: mocks.threads }));
vi.mock("@/lib/status", () => ({ reportStatus: mocks.reportStatus }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: mocks.api, getUser: () => "tester" };
});

import { ChatSidebar } from "@/components/chat-sidebar";

// folder is "" for an unfiled chat, never null: the ungrouped section
// renders threads.filter(t => t.folder === "") and a null drops the row
const row = (id: string, title: string, folder = "") => ({
  id,
  title,
  folder,
  engagement_id: null,
  updated_at: "2026-08-01T00:00:00+00:00",
});

let threadRows = [row("a", "Alpha"), row("b", "Bravo")];
let folderRows: string[] = [];

beforeEach(() => {
  vi.clearAllMocks();
  threadRows = [row("a", "Alpha"), row("b", "Bravo")];
  folderRows = [];
  mocks.threads.mockImplementation(() => Promise.resolve(threadRows));
  mocks.api.mockImplementation((path: string) =>
    Promise.resolve(path === "/api/chats/folders" ? folderRows : []),
  );
});

async function enterSelectMode(props: Partial<{ threadId: string; onNew: () => void }> = {}) {
  render(
    <ChatSidebar
      threadId={props.threadId ?? ""}
      onOpen={() => {}}
      onNew={props.onNew ?? (() => {})}
    />,
  );
  await screen.findByText("Alpha");
  fireEvent.click(screen.getByLabelText("Chat list options"));
  fireEvent.click(screen.getByText("Select…"));
}

async function confirmBulkDelete() {
  fireEvent.click(screen.getByText("Delete…"));
  fireEvent.click(
    await screen.findByRole("button", { name: /^Delete \d/ }),
  );
}

describe("deletion consequences", () => {
  it("states what one chat deletion removes and restores focus after cancellation", async () => {
    render(
      <ChatSidebar threadId="" onOpen={() => {}} onNew={() => {}} />,
    );
    await screen.findByText("Alpha");
    const trigger = screen.getByLabelText("More actions for Alpha");
    fireEvent.click(trigger);
    fireEvent.click(screen.getByText("Delete…"));

    expect(
      await screen.findByRole("button", {
        name: /Delete this chat.*messages and flock history.*Backups can still contain this chat data/i,
      }),
    ).toBeTruthy();
    expect(
      mocks.api.mock.calls.filter(([, opts]) => opts?.method === "DELETE"),
    ).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "Cancel deletion" }));
    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });

  it("says a deleted folder leaves its chats unfiled", async () => {
    threadRows = [row("a", "Alpha", "Plans")];
    folderRows = ["Plans"];
    await enterSelectMode();
    fireEvent.click(screen.getByLabelText("Select folder Plans"));
    fireEvent.click(screen.getByText("Delete…"));

    expect(
      await screen.findByRole("button", {
        name: /Delete 1 folder.*Chats in the selected folder will stay and become unfiled/i,
      }),
    ).toBeTruthy();
  });

  it("distinguishes deleted chats from chats kept by selected folders", async () => {
    threadRows = [
      row("a", "Alpha", "Plans"),
      row("b", "Bravo", "Plans"),
    ];
    folderRows = ["Plans"];
    await enterSelectMode();
    fireEvent.click(screen.getByLabelText("Select Alpha"));
    fireEvent.click(screen.getByLabelText("Select folder Plans"));
    fireEvent.click(screen.getByText("Delete…"));

    expect(
      await screen.findByRole("button", {
        name: /Delete 1 chat and 1 folder.*messages and flock history.*Other chats in the selected folder will stay and become unfiled.*Backups can still contain deleted chat data/i,
      }),
    ).toBeTruthy();
  });
});

describe("deleting several chats at once", () => {
  it("deletes every selected chat and leaves select mode", async () => {
    await enterSelectMode();
    fireEvent.click(screen.getByLabelText("Select Alpha"));
    fireEvent.click(screen.getByLabelText("Select Bravo"));
    await confirmBulkDelete();

    await waitFor(() => expect(screen.queryByText("Delete…")).toBeNull());
    const deleted = mocks.api.mock.calls
      .filter(([, opts]) => opts?.method === "DELETE")
      .map(([path]) => path);
    expect(deleted).toEqual(["/api/chats/a", "/api/chats/b"]);
    expect(mocks.reportStatus).not.toHaveBeenCalled();
  });

  it("keeps only the undeleted chats selected when one request fails", async () => {
    // the retry must not re-issue the DELETE that already succeeded
    mocks.api.mockImplementation((path: string, opts?: { method?: string }) => {
      if (opts?.method !== "DELETE") return Promise.resolve([]);
      return path === "/api/chats/b"
        ? Promise.reject(new Error("chat store exploded"))
        : Promise.resolve({});
    });
    await enterSelectMode();
    fireEvent.click(screen.getByLabelText("Select Alpha"));
    fireEvent.click(screen.getByLabelText("Select Bravo"));
    await confirmBulkDelete();

    await waitFor(() => expect(mocks.reportStatus).toHaveBeenCalled());
    expect(mocks.reportStatus.mock.calls[0][0]).toContain("chat store exploded");
    // still in select mode, and the count names ONE survivor, not two
    expect(await screen.findByText("1 chat")).toBeTruthy();
  });

  it("opens a new chat only when the open one was really deleted", async () => {
    const onNew = vi.fn();
    await enterSelectMode({ threadId: "a", onNew });
    fireEvent.click(screen.getByLabelText("Select Alpha"));
    await confirmBulkDelete();
    await waitFor(() => expect(onNew).toHaveBeenCalled());
  });

  it("leaves the open chat open when its delete failed", async () => {
    // firing onNew here would blank a transcript that still exists
    const onNew = vi.fn();
    mocks.api.mockImplementation((_p: string, opts?: { method?: string }) =>
      opts?.method === "DELETE"
        ? Promise.reject(new Error("nope"))
        : Promise.resolve([]),
    );
    await enterSelectMode({ threadId: "a", onNew });
    fireEvent.click(screen.getByLabelText("Select Alpha"));
    await confirmBulkDelete();
    await waitFor(() => expect(mocks.reportStatus).toHaveBeenCalled());
    expect(onNew).not.toHaveBeenCalled();
  });

  it("asks before deleting, and Cancel deletion keeps the selection", async () => {
    await enterSelectMode();
    fireEvent.click(screen.getByLabelText("Select Alpha"));
    const deleteButton = screen.getByText("Delete…").closest("button")!;
    fireEvent.click(deleteButton);
    fireEvent.click(await screen.findByText("Cancel deletion"));
    expect(
      mocks.api.mock.calls.filter(([, o]) => o?.method === "DELETE"),
    ).toHaveLength(0);
    expect(screen.getByText("1 chat")).toBeTruthy();
    await waitFor(() =>
      expect(document.activeElement).toBe(
        screen.getByText("Delete…").closest("button"),
      ),
    );
  });

  it("Escape cancels only the armed deletion and keeps the selection", async () => {
    await enterSelectMode();
    fireEvent.click(screen.getByLabelText("Select Alpha"));
    const trigger = screen.getByText("Delete…").closest("button")!;
    fireEvent.click(trigger);

    fireEvent.keyDown(await screen.findByRole("button", { name: /^Delete 1 chat/ }), {
      key: "Escape",
    });

    expect(
      mocks.api.mock.calls.filter(([, opts]) => opts?.method === "DELETE"),
    ).toHaveLength(0);
    expect(screen.getByText("1 chat")).toBeTruthy();
    const restored = screen.getByText("Delete…").closest("button")!;
    await waitFor(() => expect(document.activeElement).toBe(restored));
  });

  it("cannot start a delete with nothing selected", async () => {
    await enterSelectMode();
    expect(screen.getByText("0 selected")).toBeTruthy();
    expect(screen.getByText("Delete…").closest("button")).toHaveProperty(
      "disabled",
      true,
    );
  });
});
