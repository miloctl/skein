import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** The shared-chat composer, as a person types at it.
 *
 *  Three things it does that the room's message list cannot show on its own:
 *  "@bac" + Enter completes the agent instead of sending a message that
 *  calls nobody; the sent message appears at once, before the POST answers;
 *  and an agent that is still answering shows the words it has so far. */

const state = vi.hoisted(() => ({
  messages: [] as Array<Record<string, unknown>>,
  runs: [] as Array<Record<string, unknown>>,
  requests: [] as Array<{ path: string; method: string; body: Record<string, unknown> | null }>,
  // the POST resolves only when the test says so: a slow round trip is the case
  answerPost: null as null | ((ok: boolean) => void),
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
    { person: "mira", role: "steward", joined_at: "2026-08-24T11:00:00+00:00" },
    { person: "backend-architect", role: "member", kind: "agent", joined_at: "2026-08-24T11:30:00+00:00" },
    { person: "code-reviewer", role: "member", kind: "agent", joined_at: "2026-08-24T11:31:00+00:00" },
  ],
  pending_invitations: [],
};

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      state.requests.push({ path, method, body });
      if (path === "/api/users" || path === "/api/personas") return Promise.resolve([]);
      if (path === "/api/shared-chats/shared-room") return Promise.resolve(detail);
      if (path.startsWith("/api/shared-chats/shared-room/agent-runs"))
        return Promise.resolve(state.runs);
      if (path.startsWith("/api/shared-chats/shared-room/messages") && method === "GET")
        return Promise.resolve(state.messages);
      if (path === "/api/shared-chats/shared-room/messages" && method === "POST") {
        return new Promise((resolve, reject) => {
          state.answerPost = (ok) => {
            if (!ok) return reject(new Error("message refused"));
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
            resolve(message);
          };
        });
      }
      return Promise.resolve({});
    },
  };
});

import { SharedChat } from "@/components/shared-chat";

const composer = () => screen.getByLabelText("Message Launch room") as HTMLTextAreaElement;
const posts = () => state.requests.filter((r) => r.method === "POST" && r.path.endsWith("/messages"));

beforeEach(() => {
  state.messages = [];
  state.runs = [];
  state.requests = [];
  state.answerPost = null;
});
afterEach(() => vi.useRealTimers());

async function open() {
  render(<SharedChat threadId="shared-room" />);
  await screen.findByRole("heading", { name: "Launch room" });
}

describe("@ completion", () => {
  it.each(["Enter", "Tab", "click"])("completes before existing text with %s without sending", async (key) => {
    await open();
    const box = composer();
    fireEvent.change(box, { target: { value: "@bac plan it" } });
    box.focus();
    fireEvent.select(box, { target: { selectionStart: 4, selectionEnd: 4 } });
    const option = screen.getByRole("option", { name: "Insert @backend-architect" });
    if (key === "click") fireEvent.click(option);
    else fireEvent.keyDown(box, { key });
    expect(box.value).toBe("@backend-architect plan it");
    expect(document.activeElement).toBe(box);
    expect(box.selectionStart).toBe("@backend-architect ".length);
    expect(box.selectionEnd).toBe(box.selectionStart);
    expect(posts()).toHaveLength(0);
    expect(screen.queryByRole("option")).toBeNull();
  });

  it("moves the caret past an existing full mention when completion leaves the text unchanged", async () => {
    await open();
    const box = composer();
    fireEvent.change(box, { target: { value: "@backend-architect plan it" } });
    box.focus();
    fireEvent.select(box, { target: { selectionStart: 4, selectionEnd: 4 } });
    expect(screen.getByRole("option", { name: "Insert @backend-architect" })).toBeTruthy();
    fireEvent.keyDown(box, { key: "Tab" });
    expect(box.value).toBe("@backend-architect plan it");
    expect(box.selectionStart).toBe("@backend-architect ".length);
    expect(box.selectionEnd).toBe(box.selectionStart);
    expect(document.activeElement).toBe(box);
    expect(posts()).toHaveLength(0);
    expect(screen.queryByRole("option")).toBeNull();
  });

  it("replaces the whole mention when the caret is inside the token", async () => {
    await open();
    const box = composer();
    fireEvent.change(box, { target: { value: "ask @bac-old plan it" } });
    box.focus();
    fireEvent.select(box, { target: { selectionStart: 8, selectionEnd: 8 } });
    expect(screen.getByRole("option", { name: "Insert @backend-architect" })).toBeTruthy();
    fireEvent.keyDown(box, { key: "Tab" });
    expect(box.value).toBe("ask @backend-architect plan it");
    expect(box.selectionStart).toBe("ask @backend-architect ".length);
    expect(posts()).toHaveLength(0);
  });

  it("closes for a selection and follows cursor movement without changing the draft", async () => {
    await open();
    const box = composer();
    fireEvent.change(box, { target: { value: "@bac" } });
    expect(screen.getByRole("option", { name: "Insert @backend-architect" })).toBeTruthy();
    box.focus();
    fireEvent.select(box, { target: { selectionStart: 1, selectionEnd: 4 } });
    expect(screen.queryByRole("option")).toBeNull();
    fireEvent.keyDown(box, { key: "Tab" });
    expect(box.value).toBe("@bac");
    fireEvent.select(box, { target: { selectionStart: 0, selectionEnd: 0 } });
    expect(screen.queryByRole("option")).toBeNull();
    fireEvent.select(box, { target: { selectionStart: 4, selectionEnd: 4 } });
    expect(screen.getByRole("option", { name: "Insert @backend-architect" })).toBeTruthy();
  });

  it("completes the matching agent on Enter instead of sending the fragment", async () => {
    await open();
    fireEvent.change(composer(), { target: { value: "@bac" } });
    const options = screen.getAllByRole("option");
    expect(options.map((o) => o.textContent)).toEqual(["@backend-architect"]);
    fireEvent.keyDown(composer(), { key: "Enter" });
    expect(composer().value).toBe("@backend-architect ");
    expect(posts()).toHaveLength(0);
    // the token is closed, so the next Enter sends
    fireEvent.change(composer(), { target: { value: "@backend-architect plan it" } });
    expect(screen.queryByRole("option")).toBeNull();
    fireEvent.keyDown(composer(), { key: "Enter" });
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(posts()[0].body).toMatchObject({ invoke_agents: ["backend-architect"] });
  });

  it("walks the matches with the arrows and completes with Tab", async () => {
    await open();
    // a bare "@" offers every invited agent
    fireEvent.change(composer(), { target: { value: "@" } });
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual([
      "@backend-architect",
      "@code-reviewer",
    ]);
    fireEvent.keyDown(composer(), { key: "ArrowDown" });
    expect(screen.getByRole("option", { selected: true }).textContent).toBe("@code-reviewer");
    fireEvent.keyDown(composer(), { key: "Tab" });
    expect(composer().value).toBe("@code-reviewer ");
  });

  it("completes on a chip click instead of prepending a second mention", async () => {
    await open();
    fireEvent.change(composer(), { target: { value: "@bac" } });
    fireEvent.click(screen.getByRole("option", { name: "Insert @backend-architect" }));
    expect(composer().value).toBe("@backend-architect ");
  });

  it("sends a slug typed in full on the first Enter", async () => {
    await open();
    fireEvent.change(composer(), { target: { value: "@backend-architect" } });
    fireEvent.keyDown(composer(), { key: "Enter" });
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(posts()[0].body).toMatchObject({ invoke_agents: ["backend-architect"] });
  });

  it("leaves Shift+Tab to focus movement", async () => {
    await open();
    fireEvent.change(composer(), { target: { value: "@bac" } });
    fireEvent.keyDown(composer(), { key: "Tab", shiftKey: true });
    expect(composer().value).toBe("@bac");
  });
});

describe("sending", () => {
  it("shows the message at once and empties the box before the POST answers", async () => {
    await open();
    fireEvent.change(composer(), { target: { value: "ship it" } });
    fireEvent.keyDown(composer(), { key: "Enter" });
    await waitFor(() => expect(state.answerPost).not.toBeNull());
    expect(composer().value).toBe("");
    const bubble = screen.getByText("ship it").closest("article")!;
    expect(bubble.getAttribute("aria-busy")).toBe("true");
    expect(bubble.textContent).toContain("Sending…");
    await act(async () => state.answerPost!(true));
    await waitFor(() =>
      expect(screen.getByText("ship it").closest("article")!.getAttribute("aria-busy")).toBeNull(),
    );
    expect(screen.getAllByText("ship it")).toHaveLength(1);
  });

  it("takes the message back into the box when the POST fails", async () => {
    await open();
    fireEvent.change(composer(), { target: { value: "ship it" } });
    fireEvent.keyDown(composer(), { key: "Enter" });
    await waitFor(() => expect(state.answerPost).not.toBeNull());
    await act(async () => state.answerPost!(false));
    await waitFor(() => expect(composer().value).toBe("ship it"));
    expect(screen.queryByText("Sending…")).toBeNull();
    expect(screen.getByRole("alert").textContent).toContain("message refused");
  });
});

describe("an agent still answering", () => {
  it("shows the words it has so far under its status line", async () => {
    state.messages = [
      {
        id: 1,
        thread_id: "shared-room",
        role: "user",
        author_kind: "human",
        author: "mira",
        content: "@backend-architect plan it",
        created_at: "2026-08-24T12:00:00+00:00",
        turn_id: "turn-one",
        reply_to_message_id: null,
      },
    ];
    state.runs = [
      {
        turn_id: "turn-one",
        batch_id: "turn-one",
        trigger_message_id: 1,
        response_message_id: null,
        agent: "backend-architect",
        requested_by: "mira",
        status: "running",
        requested_at: "2026-08-24T12:00:00+00:00",
        started_at: "2026-08-24T12:00:01+00:00",
        finished_at: null,
        error_code: "",
        partial_text: "Start with the schema:",
      },
    ];
    await open();
    await screen.findByText("backend-architect is responding…");
    expect(screen.getByText("Start with the schema:")).toBeTruthy();
  });
});
