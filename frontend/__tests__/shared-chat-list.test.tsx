import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  user: "mira",
  strong: true,
  failSharedReads: false,
  deferNextRooms: false,
  resolveRooms: null as null | ((rooms: Array<Record<string, unknown>>) => void),
  rooms: [
    {
      id: "shared-room",
      title: "Launch room",
      created_by: "mira",
      created_at: "2026-08-24T11:00:00+00:00",
      updated_at: "2026-08-24T12:00:00+00:00",
      archived_at: null,
      role: "steward",
      member_count: 2,
      unread_count: 3,
    },
  ] as Array<Record<string, unknown>>,
  invitations: [
    { id: 4, invited_by: "dana", created_at: "2026-08-24T12:00:00+00:00" },
  ] as Array<Record<string, unknown>>,
  requests: [] as Array<{ path: string; method: string; body: unknown }>,
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      state.requests.push({ path, method, body });
      if (path === "/api/whoami")
        return Promise.resolve({ user: state.user, strong: state.strong });
      if ((!state.strong || state.failSharedReads) && path.startsWith("/api/shared-chats"))
        return Promise.reject(new Error("private chat read failed"));
      if (path === "/api/shared-chats" && method === "GET") {
        if (state.deferNextRooms) {
          state.deferNextRooms = false;
          return new Promise((resolve) => {
            state.resolveRooms = resolve;
          });
        }
        return Promise.resolve(state.rooms);
      }
      if (path === "/api/shared-chats/invitations" && method === "GET")
        return Promise.resolve(state.invitations);
      if (path === "/api/shared-chats" && method === "POST") {
        const room = { ...state.rooms[0], id: "shared-new", title: body.title, unread_count: 0 };
        state.rooms.unshift(room);
        return Promise.resolve({ ...room, members: [] });
      }
      if (path === "/api/shared-chats/invitations/4/accept") {
        state.invitations = [];
        return Promise.resolve({ id: "shared-invited", title: "Accepted room" });
      }
      if (path === "/api/shared-chats/invitations/4/decline") {
        state.invitations = [];
        return Promise.resolve({ status: "declined" });
      }
      return Promise.resolve({});
    },
  };
});

import { SharedChatList } from "@/components/shared-chat-list";

beforeEach(() => {
  state.user = "mira";
  state.strong = true;
  state.failSharedReads = false;
  state.deferNextRooms = false;
  state.resolveRooms = null;
  state.rooms = [
    {
      id: "shared-room",
      title: "Launch room",
      created_by: "mira",
      created_at: "2026-08-24T11:00:00+00:00",
      updated_at: "2026-08-24T12:00:00+00:00",
      archived_at: null,
      role: "steward",
      member_count: 2,
      unread_count: 3,
    },
  ];
  state.invitations = [
    { id: 4, invited_by: "dana", created_at: "2026-08-24T12:00:00+00:00" },
  ];
  state.requests.length = 0;
});

describe("private shared chat list", () => {
  it("states the strong-identity boundary instead of showing a false empty list", async () => {
    state.strong = false;
    render(<SharedChatList activeId="" onOpen={() => {}} />);
    expect(
      await screen.findByText(
        "Private shared chats require deployment sign-in or a personal API key.",
      ),
    ).toBeTruthy();
    expect(screen.queryByText("No private shared chats yet.")).toBeNull();
    expect(screen.getByRole("link", { name: "Open Settings & access" })).toBeTruthy();
  });

  it("clears private room names before a different identity refetches", async () => {
    render(<SharedChatList activeId="" onOpen={() => {}} />);
    expect(await screen.findByRole("button", { name: "Open Launch room, 3 unread" })).toBeTruthy();

    state.deferNextRooms = true;
    act(() => window.dispatchEvent(new Event("skein-shared-chat-activity")));
    await waitFor(() => expect(state.resolveRooms).not.toBeNull());

    state.user = "dana";
    state.failSharedReads = true;
    // A synthetic storage ping (sidebar toggle, theme) must not reset the list.
    act(() => window.dispatchEvent(new Event("storage")));
    expect(screen.getByRole("button", { name: "Open Launch room, 3 unread" })).toBeTruthy();
    act(() => window.dispatchEvent(new Event("skein-identity-change")));
    expect(screen.queryByRole("button", { name: "Open Launch room, 3 unread" })).toBeNull();
    expect(await screen.findByRole("alert")).toBeTruthy();

    act(() => state.resolveRooms?.(state.rooms));
    await Promise.resolve();
    expect(screen.queryByRole("button", { name: "Open Launch room, 3 unread" })).toBeNull();
  });

  it("shows unread private rooms and creates a new one", async () => {
    const opened: string[] = [];
    render(<SharedChatList activeId="" onOpen={(id) => opened.push(id)} />);
    expect(await screen.findByRole("button", { name: "Open Launch room, 3 unread" })).toBeTruthy();
    expect(screen.getByText("2 participants")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "New private shared chat" }));
    fireEvent.change(screen.getByLabelText("Private shared chat title"), {
      target: { value: "Decision room" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create private shared chat" }));

    await waitFor(() => expect(opened).toContain("shared-new"));
    expect(state.requests).toContainEqual({
      path: "/api/shared-chats",
      method: "POST",
      body: { title: "Decision room" },
    });
  });

  it("tells an invitee that acceptance opens the complete earlier history", async () => {
    const opened: string[] = [];
    render(<SharedChatList activeId="" onOpen={(id) => opened.push(id)} />);
    expect(await screen.findByText("Private chat invitation from dana")).toBeTruthy();
    expect(
      screen.getByText("If you accept, you can read every earlier message in that chat."),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "Decline invitation from dana" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Accept invitation from dana" }));
    await waitFor(() => expect(opened).toEqual(["shared-invited"]));
  });
});
