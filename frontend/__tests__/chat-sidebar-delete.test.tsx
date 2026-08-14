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
const row = (id: string, title: string) => ({
  id,
  title,
  folder: "",
  engagement_id: null,
  updated_at: "2026-08-01T00:00:00+00:00",
});

beforeEach(() => {
  vi.clearAllMocks();
  mocks.threads.mockResolvedValue([row("a", "Alpha"), row("b", "Bravo")]);
  mocks.api.mockResolvedValue([]);
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
  fireEvent.click(await screen.findByText(/Really delete/));
}

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

  it("asks before deleting, and Keep cancels without a request", async () => {
    await enterSelectMode();
    fireEvent.click(screen.getByLabelText("Select Alpha"));
    fireEvent.click(screen.getByText("Delete…"));
    fireEvent.click(await screen.findByText("Keep"));
    expect(
      mocks.api.mock.calls.filter(([, o]) => o?.method === "DELETE"),
    ).toHaveLength(0);
    // the selection survives the cancel
    expect(screen.getByText("1 chat")).toBeTruthy();
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
