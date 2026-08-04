import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

/** A failed /api/chats/folders fetch used to be swallowed whole: groups came
 *  only from the fetched list, the t.folder === folder filter matched no
 *  group, and every FILED chat vanished from the sidebar with no error —
 *  server-held data rendered as deleted. Groups now derive from the threads'
 *  own folder fields, so that failure costs only empty folders. */

vi.mock("@/lib/chat-threads", () => ({
  chatThreads: () =>
    Promise.resolve([
      {
        id: "t1",
        title: "Filed chat",
        folder: "ops",
        engagement_id: null,
        updated_at: "2026-08-01T00:00:00+00:00",
      },
    ]),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string) =>
      path.startsWith("/api/chats/folders")
        ? Promise.reject(new Error("folder store exploded"))
        : Promise.resolve([]),
  };
});

import { ChatSidebar } from "@/components/chat-sidebar";

describe("the sidebar when the folders fetch fails", () => {
  it("still shows every filed chat, and says what failed", async () => {
    render(<ChatSidebar threadId="" onOpen={() => {}} onNew={() => {}} />);
    expect(await screen.findByText("Filed chat")).toBeTruthy();
    expect(await screen.findByText(/📁 ops/)).toBeTruthy();
    expect(
      await screen.findByText(/Could not load this page\. folder store exploded/),
    ).toBeTruthy();
  });
});
