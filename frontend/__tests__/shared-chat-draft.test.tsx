import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** A draft typed in one room was gone after a visit to another: the text
 *  lived in component state. It is kept per person and room for the tab
 *  (components/shared-chat.tsx) and leaves with the sign-out (lib/auth.ts). */

const rooms: Record<string, Record<string, unknown>> = {
  "shared-room": { id: "shared-room", title: "Launch room" },
  "other-room": { id: "other-room", title: "Other room" },
};
const detailFor = (id: string) => ({
  ...rooms[id],
  kind: "shared",
  created_by: "mira",
  created_at: "2026-08-24T11:00:00+00:00",
  updated_at: "2026-08-24T12:00:00+00:00",
  archived_at: null,
  viewer: "mira",
  role: "steward",
  members: [{ person: "mira", role: "steward", joined_at: "2026-08-24T11:00:00+00:00" }],
  pending_invitations: [],
});

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    getUser: () => "mira",
    api: (path: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (path === "/api/users" || path === "/api/personas") return Promise.resolve([]);
      const room = Object.keys(rooms).find((id) => path.startsWith(`/api/shared-chats/${id}`));
      if (room && path === `/api/shared-chats/${room}`) return Promise.resolve(detailFor(room));
      if (room && path.includes("/agent-runs")) return Promise.resolve([]);
      if (room && path.includes("/messages") && method === "GET") return Promise.resolve([]);
      if (room && path.endsWith("/messages") && method === "POST")
        return Promise.resolve({
          id: 1, thread_id: room, role: "user", author_kind: "human", author: "mira",
          content: JSON.parse(String(init?.body)).message, created_at: "2026-08-24T12:01:00+00:00",
          turn_id: "", reply_to_message_id: null,
        });
      return Promise.resolve({});
    },
  };
});

import { SharedChat } from "@/components/shared-chat";

const box = (title: string) => screen.getByLabelText(`Message ${title}`) as HTMLTextAreaElement;

beforeEach(() => window.sessionStorage.clear());
afterEach(() => cleanup());

describe("a shared-chat draft", () => {
  it("survives a room switch and comes back, per room", async () => {
    const view = render(<SharedChat threadId="shared-room" />);
    await screen.findByRole("heading", { name: "Launch room" });
    fireEvent.change(box("Launch room"), { target: { value: "hello from the launch room" } });
    view.rerender(<SharedChat threadId="other-room" />);
    await screen.findByRole("heading", { name: "Other room" });
    expect(box("Other room").value).toBe("");
    fireEvent.change(box("Other room"), { target: { value: "other draft" } });
    view.rerender(<SharedChat threadId="shared-room" />);
    await screen.findByRole("heading", { name: "Launch room" });
    expect(box("Launch room").value).toBe("hello from the launch room");
    expect(window.sessionStorage.getItem("skein-shared-draft:mira:other-room")).toBe("other draft");
  });

  it("comes back after a remount of the same room", async () => {
    // the chat page mounts a room with key={threadId}, so a switch remounts
    render(<SharedChat threadId="shared-room" />);
    await screen.findByRole("heading", { name: "Launch room" });
    fireEvent.change(box("Launch room"), { target: { value: "still here" } });
    cleanup();
    render(<SharedChat threadId="shared-room" />);
    await screen.findByRole("heading", { name: "Launch room" });
    expect(box("Launch room").value).toBe("still here");
  });

  it("is forgotten once the message is sent", async () => {
    render(<SharedChat threadId="shared-room" />);
    await screen.findByRole("heading", { name: "Launch room" });
    fireEvent.change(box("Launch room"), { target: { value: "ship it" } });
    expect(window.sessionStorage.getItem("skein-shared-draft:mira:shared-room")).toBe("ship it");
    await act(async () => {
      fireEvent.keyDown(box("Launch room"), { key: "Enter" });
    });
    expect(window.sessionStorage.getItem("skein-shared-draft:mira:shared-room")).toBeNull();
  });
});
