import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  agentAdded: true,
  extraAgents: [] as string[],
  messages: [] as Array<Record<string, unknown>>,
  runs: [] as Array<Record<string, unknown>>,
  requests: [] as Array<{ path: string; method: string; body: Record<string, unknown> | null }>,
}));

const persona = {
  slug: "backend-architect",
  name: "Backend Architect",
  description: "Designs reliable backend systems.",
  emoji: "🏗️",
  vibe: "structural",
  disclosure: "Uses the configured model provider.",
};

const secondPersona = {
  ...persona,
  slug: "code-reviewer",
  name: "Code Reviewer",
  description: "Reviews code and system boundaries.",
};

function detail() {
  return {
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
      { person: "mira", role: "steward", joined_at: "2026-08-24T11:00:00+00:00" },
      ...(state.agentAdded
        ? [
            {
              person: "backend-architect",
              role: "member",
              kind: "agent",
              joined_at: "2026-08-24T11:30:00+00:00",
            },
          ]
        : []),
      ...state.extraAgents.map((agent) => ({
        person: agent,
        role: "member",
        kind: "agent",
        joined_at: "2026-08-24T11:31:00+00:00",
      })),
    ],
    pending_invitations: [],
  };
}

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      state.requests.push({ path, method, body });
      if (path === "/api/users") return Promise.resolve([]);
      if (path === "/api/personas") return Promise.resolve([persona, secondPersona]);
      if (path === "/api/shared-chats/shared-room") return Promise.resolve(detail());
      if (path.startsWith("/api/shared-chats/shared-room/agent-runs")) {
        return Promise.resolve(state.runs);
      }
      if (path.startsWith("/api/shared-chats/shared-room/messages") && method === "GET") {
        return Promise.resolve(state.messages);
      }
      if (path === "/api/shared-chats/shared-room/messages" && method === "POST") {
        const message = {
          id: state.messages.length + 1,
          thread_id: "shared-room",
          role: "user",
          author_kind: "human",
          author: "mira",
          content: body.message,
          created_at: "2026-08-24T12:01:00+00:00",
          turn_id: body.invoke_agent || body.invoke_agents?.length ? "turn-one" : "",
          reply_to_message_id: null,
        };
        state.messages.push(message);
        return Promise.resolve(message);
      }
      if (path === "/api/shared-chats/shared-room/agents" && method === "POST") {
        if (body.agent === "backend-architect") state.agentAdded = true;
        else if (!state.extraAgents.includes(body.agent)) state.extraAgents.push(body.agent);
        return Promise.resolve(detail());
      }
      if (path.endsWith("/read")) return Promise.resolve({});
      return Promise.resolve({});
    },
  };
});

import { SharedChat } from "@/components/shared-chat";

beforeEach(() => {
  state.agentAdded = true;
  state.extraAgents = [];
  state.messages = [];
  state.runs = [];
  state.requests = [];
});

afterEach(() => vi.useRealTimers());

describe("one governed shared-chat agent", () => {
  it("states the history and provider boundary before a steward adds an agent", async () => {
    state.agentAdded = false;
    render(<SharedChat threadId="shared-room" />);
    await screen.findByRole("heading", { name: "Launch room" });
    fireEvent.click(screen.getByRole("button", { name: "Manage participants" }));
    fireEvent.change(await screen.findByLabelText("Agent to add"), {
      target: { value: "backend-architect" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Review agent access" }));

    expect(
      screen.getByText(
        "Backend Architect can read this private chat history when a participant calls @backend-architect.",
      ),
    ).toBeTruthy();
    expect(
      screen.getByText("The chat history goes to the configured model provider."),
    ).toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", { name: "Add Backend Architect to this private chat" }),
    );
    await waitFor(() =>
      expect(state.requests).toContainEqual({
        path: "/api/shared-chats/shared-room/agents",
        method: "POST",
        body: { agent: "backend-architect", share_history: true },
      }),
    );
    await waitFor(() =>
      expect(document.activeElement).toBe(
        screen.getByRole("heading", { name: "Participants and access" }),
      ),
    );
  });

  it("invokes the added agent only when the message starts with its mention", async () => {
    render(<SharedChat threadId="shared-room" />);
    await screen.findByRole("heading", { name: "Launch room" });
    fireEvent.click(
      screen.getByRole("button", { name: "Call Backend Architect (@backend-architect)" }),
    );
    const composer = screen.getByLabelText("Message Launch room") as HTMLTextAreaElement;
    expect(composer.value).toBe("@backend-architect ");
    fireEvent.change(composer, {
      target: { value: "@backend-architect review the boundary" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() =>
      expect(state.requests).toContainEqual(
        expect.objectContaining({
          path: "/api/shared-chats/shared-room/messages",
          method: "POST",
          body: expect.objectContaining({ invoke_agents: ["backend-architect"] }),
        }),
      ),
    );

    fireEvent.change(composer, {
      target: { value: "Hello @backend-architect" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => {
      const posts = state.requests.filter(
        (request) => request.path.endsWith("/messages") && request.method === "POST",
      );
      expect(posts.at(-1)?.body?.invoke_agents).toEqual([]);
    });
  });

  it("keeps agent addition available until the room has four agents", async () => {
    render(<SharedChat threadId="shared-room" />);
    await screen.findByRole("heading", { name: "Launch room" });
    fireEvent.click(screen.getByRole("button", { name: "Manage participants" }));

    fireEvent.change(await screen.findByLabelText("Agent to add"), {
      target: { value: "code-reviewer" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Review agent access" }));
    fireEvent.click(
      screen.getByRole("button", { name: "Add Code Reviewer to this private chat" }),
    );

    await waitFor(() =>
      expect(state.requests).toContainEqual({
        path: "/api/shared-chats/shared-room/agents",
        method: "POST",
        body: { agent: "code-reviewer", share_history: true },
      }),
    );
  });

  it("sends every leading invited mention as one bounded agent call", async () => {
    state.extraAgents = ["code-reviewer"];
    render(<SharedChat threadId="shared-room" />);
    await screen.findByRole("heading", { name: "Launch room" });
    fireEvent.click(
      screen.getByRole("button", { name: "Call Backend Architect (@backend-architect)" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Call Code Reviewer (@code-reviewer)" }));
    const composer = screen.getByLabelText("Message Launch room") as HTMLTextAreaElement;
    expect(composer.value).toBe("@backend-architect @code-reviewer ");
    fireEvent.change(composer, {
      target: { value: "@backend-architect @code-reviewer compare the boundary" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));

    await waitFor(() => {
      const post = state.requests.find(
        (request) => request.path.endsWith("/messages") && request.method === "POST",
      );
      expect(post?.body?.invoke_agents).toEqual(["backend-architect", "code-reviewer"]);
    });
  });

  it("refreshes agent access while participant management is closed", async () => {
    vi.useFakeTimers();
    render(<SharedChat threadId="shared-room" />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(
      screen.getByRole("button", { name: "Call Backend Architect (@backend-architect)" }),
    ).toBeTruthy();

    state.agentAdded = false;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
      await Promise.resolve();
    });

    expect(
      screen.queryByRole("button", { name: "Call Backend Architect (@backend-architect)" }),
    ).toBeNull();
  });

  it("hides an obsolete failure after the same agent completes a newer call", async () => {
    state.runs = [
      {
        turn_id: "turn-old",
        trigger_message_id: 3,
        response_message_id: null,
        agent: "backend-architect",
        requested_by: "mira",
        status: "completion_unknown",
        requested_at: "2026-08-24T12:03:00+00:00",
        started_at: "2026-08-24T12:03:01+00:00",
        finished_at: "2026-08-24T12:04:00+00:00",
        error_code: "process_restarted",
      },
      {
        turn_id: "turn-new",
        trigger_message_id: 4,
        response_message_id: 5,
        agent: "backend-architect",
        requested_by: "mira",
        status: "completed",
        requested_at: "2026-08-24T12:05:00+00:00",
        started_at: "2026-08-24T12:05:01+00:00",
        finished_at: "2026-08-24T12:06:00+00:00",
        error_code: "",
      },
    ];
    render(<SharedChat threadId="shared-room" />);
    await screen.findByRole("heading", { name: "Launch room" });

    expect(screen.queryByText(/could not determine whether backend-architect/)).toBeNull();
  });

  it("states when the called agent, not the requester, became unavailable", async () => {
    state.runs = [
      {
        turn_id: "turn-refused",
        trigger_message_id: 3,
        response_message_id: null,
        agent: "backend-architect",
        requested_by: "mira",
        status: "refused",
        requested_at: "2026-08-24T12:03:00+00:00",
        started_at: null,
        finished_at: "2026-08-24T12:04:00+00:00",
        error_code: "agent_unavailable",
      },
    ];
    render(<SharedChat threadId="shared-room" />);

    expect(
      await screen.findByText(
        "backend-architect is not available. A steward can remove it, or an administrator can reactivate it.",
      ),
    ).toBeTruthy();
    expect(screen.queryByText(/requester is no longer/)).toBeNull();
  });

  it("shows durable unknown completion and labels persisted agent authorship", async () => {
    state.messages = [
      {
        id: 2,
        thread_id: "shared-room",
        role: "assistant",
        author_kind: "agent",
        author: "backend-architect",
        content: "I reviewed the boundary.",
        created_at: "2026-08-24T12:02:00+00:00",
        turn_id: "turn-one",
        reply_to_message_id: 1,
      },
    ];
    state.runs = [
      {
        turn_id: "turn-two",
        trigger_message_id: 3,
        response_message_id: null,
        agent: "backend-architect",
        requested_by: "mira",
        status: "completion_unknown",
        requested_at: "2026-08-24T12:03:00+00:00",
        started_at: "2026-08-24T12:03:01+00:00",
        finished_at: "2026-08-24T12:04:00+00:00",
        error_code: "process_restarted",
      },
    ];
    render(<SharedChat threadId="shared-room" />);

    expect(
      (await screen.findAllByText("backend-architect · agent", { exact: false })).length,
    ).toBeGreaterThan(0);
    expect(
      screen.getByText(
        "Skein could not determine whether backend-architect completed the response. Send a new @backend-architect message if you still need it.",
      ),
    ).toBeTruthy();
  });
});
