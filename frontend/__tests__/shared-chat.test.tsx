import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  messages: [
    {
      id: 1,
      thread_id: "shared-room",
      role: "user",
      author_kind: "human",
      author: "dana",
      content: "First private message",
      created_at: "2026-08-24T12:00:00+00:00",
      turn_id: "",
      reply_to_message_id: null,
    },
  ] as Array<Record<string, unknown>>,
  requests: [] as Array<{
    path: string;
    method: string;
    body: unknown;
    cache?: RequestCache;
  }>,
  failPost: false,
  engagementId: null as number | null,
}));

const detail = {
  id: "shared-room",
  kind: "shared",
  title: "Launch room",
  created_by: "mira",
  created_at: "2026-08-24T11:00:00+00:00",
  updated_at: "2026-08-24T12:00:00+00:00",
  archived_at: null,
  viewer: "mira",
  role: "steward",
  members: [
    {
      person: "mira",
      role: "steward",
      joined_at: "2026-08-24T11:00:00+00:00",
      last_read_message_id: 0,
    },
    {
      person: "dana",
      role: "member",
      joined_at: "2026-08-24T11:30:00+00:00",
      last_read_message_id: 0,
    },
  ],
  pending_invitations: [
    {
      id: 7,
      person: "river",
      invited_by: "mira",
      created_at: "2026-08-24T11:45:00+00:00",
    },
  ],
};

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      state.requests.push({
        path,
        method,
        body,
        ...(init?.cache ? { cache: init.cache } : {}),
      });
      if (path === "/api/users" || path === "/api/personas") return Promise.resolve([]);
      if (path === "/api/engagements") {
        return Promise.resolve([
          { id: 5, name: "Open delivery", visibility: "workspace" },
          { id: 6, name: "Private delivery", visibility: "private" },
        ]);
      }
      if (path === "/api/shared-chats/shared-room" && method === "PATCH") {
        state.engagementId = body.engagement_id || null;
        return Promise.resolve({
          ...detail,
          engagement_id: state.engagementId,
          engagement_name: state.engagementId ? "Open delivery" : "",
        });
      }
      if (path.startsWith("/api/shared-chats/shared-room/agent-runs")) {
        return Promise.resolve([]);
      }
      if (path === "/api/shared-chats/shared-room") {
        return Promise.resolve({
          ...detail,
          engagement_id: state.engagementId,
          engagement_name: state.engagementId ? "Open delivery" : "",
          members: detail.members.map((member) => ({ ...member })),
          pending_invitations: detail.pending_invitations.map((invitation) => ({
            ...invitation,
          })),
        });
      }
      if (path.startsWith("/api/shared-chats/shared-room/messages") && method === "GET") {
        const url = new URL(path, "http://skein.test");
        const after = Number(url.searchParams.get("after") ?? 0);
        const before = Number(url.searchParams.get("before") ?? 0);
        return Promise.resolve(
          before
            ? state.messages.filter((message) => Number(message.id) < before)
            : state.messages.filter((message) => Number(message.id) > after),
        );
      }
      if (path === "/api/shared-chats/shared-room/messages" && method === "POST") {
        if (state.failPost) return Promise.reject(new Error("message refused"));
        const message = {
          id: state.messages.length + 1,
          thread_id: "shared-room",
          role: "user",
          author_kind: "human",
          author: "mira",
          content: body.message,
          created_at: "2026-08-24T12:01:00+00:00",
          turn_id: "",
          reply_to_message_id: null,
        };
        state.messages.push(message);
        return Promise.resolve(message);
      }
      if (path.endsWith("/read")) return Promise.resolve({});
      const deleteTarget = path.match(/\/messages\/(\d+)$/);
      if (deleteTarget && method === "DELETE") {
        const message = state.messages.find(
          (row) => Number(row.id) === Number(deleteTarget[1]),
        );
        if (!message) return Promise.reject(new Error("No message was found."));
        message.content = "";
        message.deleted_at = "2026-08-24T12:05:00+00:00";
        return Promise.resolve({ ...message });
      }
      if (path.endsWith("/invitations") && method === "POST") {
        return Promise.resolve({ id: 9, person: body.person, created_at: "now" });
      }
      if (path.endsWith("/archive")) return Promise.resolve({ ...detail, archived_at: "now" });
      if (path.endsWith("/restore")) return Promise.resolve(detail);
      if (path.endsWith("/leave")) return Promise.resolve({ left: true });
      if (path.endsWith("/members") && method === "DELETE") return Promise.resolve({ left: true });
      if (path.endsWith("/members/role")) return Promise.resolve(body);
      return Promise.resolve({});
    },
  };
});

import { SharedChat } from "@/components/shared-chat";

beforeEach(() => {
  window.history.replaceState({}, "", "/chat");
  state.messages.splice(1);
  state.requests.length = 0;
  state.failPost = false;
  state.engagementId = null;
  detail.members = [
    {
      person: "mira",
      role: "steward",
      joined_at: "2026-08-24T11:00:00+00:00",
      last_read_message_id: 0,
    },
    {
      person: "dana",
      role: "member",
      joined_at: "2026-08-24T11:30:00+00:00",
      last_read_message_id: 0,
    },
  ];
  detail.pending_invitations = [
    {
      id: 7,
      person: "river",
      invited_by: "mira",
      created_at: "2026-08-24T11:45:00+00:00",
    },
  ];
});

afterEach(() => {
  vi.useRealTimers();
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    value: "visible",
  });
});

describe("private shared chat", () => {
  it("shows real authors and posts a human message without losing its draft on failure", async () => {
    render(<SharedChat threadId="shared-room" />);
    const heading = await screen.findByRole("heading", { name: "Launch room" });
    await waitFor(() => expect(document.activeElement).toBe(heading));
    expect(
      state.requests.some(
        (request) =>
          request.path === "/api/shared-chats/shared-room" && request.cache === "no-store",
      ),
    ).toBe(true);
    expect(
      state.requests.some(
        (request) =>
          request.path === "/api/shared-chats/shared-room/messages" &&
          request.cache === "no-store",
      ),
    ).toBe(true);
    expect(screen.getByText("dana")).toBeTruthy();
    expect(screen.getByText("First private message")).toBeTruthy();
    expect(screen.getByText(/Accepted people can read it/)).toBeTruthy();

    const composer = screen.getByLabelText("Message Launch room") as HTMLTextAreaElement;
    fireEvent.change(composer, { target: { value: "A new note" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    expect(await screen.findByText("A new note")).toBeTruthy();
    expect(composer.value).toBe("");
    const sent = state.requests.find(
      (request) => request.path.endsWith("/messages") && request.method === "POST",
    );
    expect(sent?.body).toMatchObject({ message: "A new note" });
    expect(String((sent?.body as { client_key: string }).client_key)).toMatch(
      /^[A-Za-z0-9_-]{1,64}$/,
    );

    state.failPost = true;
    fireEvent.change(composer, { target: { value: "Keep this draft" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(composer.value).toBe("Keep this draft"));
    const failedKey = String(
      (state.requests.at(-1)?.body as { client_key?: string } | null)?.client_key,
    );
    state.failPost = false;
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(composer.value).toBe(""));
    expect(
      (state.requests.at(-2)?.body as { client_key?: string } | null)?.client_key,
    ).toBe(failedKey);
  });

  it("clears the open private transcript as soon as the browser identity changes", async () => {
    const onUnavailable = vi.fn();
    render(<SharedChat threadId="shared-room" onUnavailable={onUnavailable} />);
    expect(await screen.findByText("First private message")).toBeTruthy();

    act(() => window.dispatchEvent(new Event("skein-identity-change")));

    expect(screen.queryByText("First private message")).toBeNull();
    expect(onUnavailable).toHaveBeenCalledOnce();
  });

  it("keeps the transcript through a synthetic storage ping, and clears on a cross-tab identity write", async () => {
    const onUnavailable = vi.fn();
    render(<SharedChat threadId="shared-room" onUnavailable={onUnavailable} />);
    expect(await screen.findByText("First private message")).toBeTruthy();

    // The sidebar toggle, theme adoption, and manage toggle all dispatch this
    // bare form for non-identity localStorage writes.
    act(() => window.dispatchEvent(new Event("storage")));
    expect(screen.getByText("First private message")).toBeTruthy();
    expect(onUnavailable).not.toHaveBeenCalled();

    act(() =>
      window.dispatchEvent(new StorageEvent("storage", { key: "skein-user" })),
    );
    expect(screen.queryByText("First private message")).toBeNull();
    expect(onUnavailable).toHaveBeenCalledOnce();
  });

  it("focuses a message named by a private chat notification and can load the history before it", async () => {
    for (const [id, content] of [
      [2, "Second private message"],
      [3, "Third private message"],
    ] as const) {
      state.messages.push({
        id,
        thread_id: "shared-room",
        role: "user",
        author_kind: "human",
        author: "dana",
        content,
        created_at: "2026-08-24T12:02:00+00:00",
        turn_id: "",
        reply_to_message_id: null,
      });
    }
    window.history.replaceState({}, "", "/chat?shared=shared-room#shared-message-2");
    render(<SharedChat threadId="shared-room" />);
    await screen.findByText("Second private message");
    expect(screen.getByText("Third private message")).toBeTruthy();
    expect(screen.queryByText("First private message")).toBeNull();

    await waitFor(() =>
      expect(document.activeElement?.id).toBe("shared-message-2"),
    );
    expect(
      state.requests.some(
        (request) =>
          request.path === "/api/shared-chats/shared-room/messages?after=1" &&
          request.cache === "no-store",
      ),
    ).toBe(true);
    // The deep link marks read only through the linked message — the rows
    // after it were fetched, not seen.
    const readBodies = state.requests
      .filter((request) => request.path.endsWith("/read"))
      .map((request) => (request.body as { message_id: number }).message_id);
    expect(readBodies).toContain(2);
    expect(readBodies).not.toContain(3);

    // The short deep-link page still has history before it.
    fireEvent.click(screen.getByRole("button", { name: "Load older messages" }));
    expect(await screen.findByText("First private message")).toBeTruthy();
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Load older messages" })).toBeNull(),
    );
  });

  it("author-deletes a message into a tombstone after a confirmation", async () => {
    render(<SharedChat threadId="shared-room" />);
    const composer = (await screen.findByLabelText(
      "Message Launch room",
    )) as HTMLTextAreaElement;
    fireEvent.change(composer, { target: { value: "Remove me later" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await screen.findByText("Remove me later");

    // dana's message (not mine) offers no delete control
    expect(screen.getAllByRole("button", { name: /^Delete your message/ })).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: /^Delete your message/ }));
    const confirm = screen.getByRole("button", { name: "Confirm: delete message" });
    expect(
      screen.getByText("This message will be removed for every participant."),
    ).toBeTruthy();
    await waitFor(() => expect(document.activeElement).toBe(confirm));
    fireEvent.click(confirm);

    expect(await screen.findByText("The author deleted this message.")).toBeTruthy();
    expect(screen.queryByText("Remove me later")).toBeNull();
    expect(screen.queryByRole("button", { name: /^Delete your message/ })).toBeNull();
    expect(
      state.requests.some(
        (request) =>
          request.method === "DELETE" &&
          request.path === "/api/shared-chats/shared-room/messages/2",
      ),
    ).toBe(true);
  });

  it("shows which other participants have seen the newest message", async () => {
    detail.members[1].last_read_message_id = 1;
    render(<SharedChat threadId="shared-room" />);
    await screen.findByText("First private message");
    expect(screen.getByText("Seen by dana.")).toBeTruthy();
  });

  it("confirms that an invitation shares the complete earlier history", async () => {
    render(<SharedChat threadId="shared-room" />);
    await screen.findByRole("heading", { name: "Launch room" });
    fireEvent.click(screen.getByRole("button", { name: "Manage participants" }));
    await waitFor(() =>
      expect(
        state.requests.filter(
          (request) =>
            request.path === "/api/shared-chats/shared-room" &&
            request.cache === "no-store",
        ).length,
      ).toBeGreaterThanOrEqual(2),
    );
    fireEvent.change(screen.getByLabelText("Invite a teammate"), {
      target: { value: "marcus" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Review invitation for marcus" }));

    expect(
      screen.getByText("marcus can read every message already in this chat."),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Send invitation to marcus" }));
    await waitFor(() =>
      expect(state.requests).toContainEqual({
        path: "/api/shared-chats/shared-room/invitations",
        method: "POST",
        body: { person: "marcus", share_history: true },
      }),
    );
  });

  it("links the private chat to a workspace engagement", async () => {
    render(<SharedChat threadId="shared-room" />);
    await screen.findByRole("heading", { name: "Launch room" });
    fireEvent.click(screen.getByRole("button", { name: "Manage participants" }));
    const picker = (await screen.findByLabelText("Linked engagement")) as HTMLSelectElement;
    expect([...picker.options].map((option) => option.text)).toEqual([
      "No linked engagement",
      "Open delivery",
    ]);
    fireEvent.change(picker, { target: { value: "5" } });

    await waitFor(() =>
      expect(state.requests).toContainEqual({
        path: "/api/shared-chats/shared-room",
        method: "PATCH",
        body: { engagement_id: 5 },
      }),
    );
    expect(await screen.findByText("Engagement: Open delivery")).toBeTruthy();
  });

  it("lets a steward change roles, remove a participant, and archive the room", async () => {
    render(<SharedChat threadId="shared-room" />);
    await screen.findByRole("heading", { name: "Launch room" });
    fireEvent.click(screen.getByRole("button", { name: "Manage participants" }));

    fireEvent.click(screen.getByRole("button", { name: "Make dana a steward" }));
    expect(
      screen.getByText("dana can manage participants, invitations, the title, and archive state."),
    ).toBeTruthy();
    const roleConfirm = screen.getByRole("button", {
      name: "Confirm: make dana a steward",
    });
    expect(document.activeElement).toBe(roleConfirm);
    fireEvent.click(roleConfirm);
    await waitFor(() =>
      expect(state.requests).toContainEqual({
        path: "/api/shared-chats/shared-room/members/role",
        method: "POST",
        body: { person: "dana", role: "steward" },
      }),
    );

    const revoke = screen.getByRole("button", {
      name: "Revoke invitation for river",
    });
    fireEvent.click(revoke);
    expect(
      screen.getByText("river can no longer accept this invitation."),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Cancel access change" }));
    await waitFor(() => expect(document.activeElement).toBe(revoke));
    fireEvent.click(revoke);
    fireEvent.click(
      screen.getByRole("button", { name: "Confirm: revoke invitation for river" }),
    );
    await waitFor(() =>
      expect(state.requests).toContainEqual({
        path: "/api/shared-chats/shared-room/invitations",
        method: "DELETE",
        body: { invitation_id: 7 },
      }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Remove dana" }));
    expect(
      screen.getByText(
        "dana will lose access to every message in this chat. Their messages stay attributed.",
      ),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Confirm: remove dana" }));
    await waitFor(() =>
      expect(state.requests).toContainEqual({
        path: "/api/shared-chats/shared-room/members",
        method: "DELETE",
        body: { person: "dana" },
      }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Archive shared chat" }));
    expect(
      screen.getByText("This chat will become read-only for every participant."),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Confirm: archive shared chat" }));
    await waitFor(() =>
      expect(state.requests.some((request) => request.path.endsWith("/archive"))).toBe(true),
    );
  });

  it("states the result before a participant leaves", async () => {
    const onLeave = vi.fn();
    render(<SharedChat threadId="shared-room" onLeave={onLeave} />);
    await screen.findByRole("heading", { name: "Launch room" });
    fireEvent.click(screen.getByRole("button", { name: "Manage participants" }));
    fireEvent.click(screen.getByRole("button", { name: "Leave shared chat" }));

    expect(
      screen.getByText(
        "You will lose access to every message in this chat. You need a new invitation to return.",
      ),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Confirm: leave shared chat" }));
    await waitFor(() => expect(onLeave).toHaveBeenCalledOnce());
  });

  it("refreshes participant access while management stays open", async () => {
    vi.useFakeTimers();
    render(<SharedChat threadId="shared-room" />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    fireEvent.click(screen.getByRole("button", { name: "Manage participants" }));
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    detail.members.push({
      person: "marcus",
      role: "member",
      joined_at: "2026-08-24T12:03:00+00:00",
      last_read_message_id: 0,
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
      await Promise.resolve();
    });

    expect(screen.getAllByText("marcus").length).toBeGreaterThan(0);
  });

  it("does not mark messages read while the tab is hidden", async () => {
    vi.useFakeTimers();
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "hidden",
    });
    render(<SharedChat threadId="shared-room" />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(
      state.requests.some((request) => request.path.endsWith("/read")),
    ).toBe(false);

    state.messages.push({
      id: 2,
      thread_id: "shared-room",
      role: "user",
      author_kind: "human",
      author: "dana",
      content: "Hidden-tab message",
      created_at: "2026-08-24T12:02:00+00:00",
      turn_id: "",
      reply_to_message_id: null,
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
      await Promise.resolve();
    });
    expect(
      state.requests.some(
        (request) =>
          request.path.endsWith("/read") &&
          (request.body as { message_id?: number })?.message_id === 2,
      ),
    ).toBe(false);

    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "visible",
    });
    act(() => document.dispatchEvent(new Event("visibilitychange")));
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(
      state.requests.some(
        (request) =>
          request.path.endsWith("/read") &&
          (request.body as { message_id?: number })?.message_id === 2,
      ),
    ).toBe(true);
  });

  it("polls for new messages and marks the newest row read", async () => {
    vi.useFakeTimers();
    render(<SharedChat threadId="shared-room" />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByText("First private message")).toBeTruthy();

    state.messages.push({
      id: 2,
      thread_id: "shared-room",
      role: "user",
      author_kind: "human",
      author: "dana",
      content: "Arrived from another browser",
      created_at: "2026-08-24T12:02:00+00:00",
      turn_id: "",
      reply_to_message_id: null,
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
      await Promise.resolve();
    });

    expect(screen.getByText("Arrived from another browser")).toBeTruthy();
    // The id keeps consecutive same-author announcements distinct — without
    // it the second one produces identical state, and the live region's DOM
    // never mutates, so a screen reader announces nothing.
    expect(screen.getByRole("status").textContent).toBe("New message 2 from dana.");
    expect(
      state.requests.some(
        (request) =>
          request.path === "/api/shared-chats/shared-room/read" &&
          (request.body as { message_id?: number })?.message_id === 2,
      ),
    ).toBe(true);
  });
});
