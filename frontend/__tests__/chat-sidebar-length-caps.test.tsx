import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

/** The rename and new-folder drafts stop at the server's own caps
 *  (chat_threads.TITLE_LEN 60, FOLDER_LEN 40). Without them a long name
 *  reached PATCH /api/chats and came back as a pydantic sentence
 *  ("body.title: String should have at most 60 characters") in the alert. */

vi.mock("@/lib/chat-threads", () => ({
  chatThreads: () =>
    Promise.resolve([
      {
        id: "t1",
        title: "Filed chat",
        folder: "",
        engagement_id: null,
        updated_at: "2026-08-01T00:00:00+00:00",
      },
    ]),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: () => Promise.resolve([]) };
});

import { ChatSidebar } from "@/components/chat-sidebar";

it("caps the rename draft at the title length the server accepts", async () => {
  render(<ChatSidebar threadId="" onOpen={() => {}} onNew={() => {}} />);
  await screen.findByText("Filed chat");
  fireEvent.click(screen.getByRole("button", { name: "More actions for Filed chat" }));
  fireEvent.click(screen.getByRole("button", { name: /Rename/ }));
  const field = screen.getByRole("textbox", { name: "New chat name" }) as HTMLInputElement;
  expect(field.maxLength).toBe(60);
});

it("caps the folder draft at the folder length the server accepts", async () => {
  render(<ChatSidebar threadId="" onOpen={() => {}} onNew={() => {}} />);
  await screen.findByText("Filed chat");
  fireEvent.click(screen.getByRole("button", { name: "Chat list options" }));
  fireEvent.click(screen.getByRole("button", { name: "New folder" }));
  const field = screen.getByRole("textbox", { name: "Folder name" }) as HTMLInputElement;
  expect(field.maxLength).toBe(40);
});
