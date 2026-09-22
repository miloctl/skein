import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** Chat needs the raw streaming response, so it calls authenticatedFetch()
 *  instead of api(). The shared request path owns identity and 401 handling.
 *  These tests pin the SSE reader and chat-specific error behavior.
 *
 *  The adapter is module-private, so the test captures it where the runtime
 *  library receives it. Everything below the capture is the real code. */

const mocks = vi.hoisted(() => ({
  authenticatedFetch: vi.fn(),
  reportStatus: vi.fn(),
  chatThreads: vi.fn(),
  api: vi.fn(),
  outgoing: vi.fn((t: string) => t),
  thread: {
    getState: vi.fn(() => ({ messages: [] as unknown[] })),
    reset: vi.fn(),
  },
  captured: null as null | {
    run: (o: {
      messages: { content: { type: string; text: string }[] }[];
      abortSignal?: AbortSignal;
    }) => AsyncGenerator<{ content: { type: string; text: string }[] }>;
  },
}));

vi.mock("@assistant-ui/react", () => ({
  AssistantRuntimeProvider: ({ children }: { children: React.ReactNode }) => (
    <>{children}</>
  ),
  useLocalRuntime: (adapter: never) => {
    mocks.captured = adapter;
    return {};
  },
  useAui: () => ({ thread: mocks.thread }),
}));

vi.mock("@/lib/api", () => ({
  api: mocks.api,
  authenticatedFetch: mocks.authenticatedFetch,
  actionError: (e: unknown) => (e as Error).message,
}));
vi.mock("@/lib/status", () => ({ reportStatus: mocks.reportStatus }));
vi.mock("@/lib/chat-threads", () => ({ chatThreads: mocks.chatThreads }));
vi.mock("@/lib/persona", () => ({ outgoing: mocks.outgoing }));

import { RuntimeProvider } from "@/app/runtime-provider";

/** One SSE body, delivered in caller-chosen network chunks. Splitting is the
 *  point: the reader must not care where a chunk boundary falls. */
function sseBody(chunks: string[]) {
  const encoder = new TextEncoder();
  let i = 0;
  return {
    getReader: () => ({
      read: async () =>
        i < chunks.length
          ? { done: false, value: encoder.encode(chunks[i++]) }
          : { done: true, value: undefined },
    }),
  };
}

function ok(chunks: string[]) {
  return { ok: true, body: sseBody(chunks), status: 200, statusText: "OK" };
}

async function drain() {
  const out: string[] = [];
  const gen = mocks.captured!.run({
    messages: [{ content: [{ type: "text", text: "hello" }] }],
  });
  for await (const step of gen) out.push(step.content[0].text);
  return out;
}

async function mountAndCapture() {
  render(
    <RuntimeProvider threadId="t1">
      <p>thread ui</p>
    </RuntimeProvider>,
  );
  await waitFor(() => expect(mocks.captured).not.toBeNull());
}

beforeEach(() => {
  mocks.authenticatedFetch.mockImplementation((path: string, init?: RequestInit) =>
    fetch(`http://backend.test${path}`, init),
  );
  mocks.chatThreads.mockResolvedValue([]);
  mocks.thread.getState.mockReturnValue({ messages: [] });
  mocks.captured = null;
});

afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

describe("a persona masthead", () => {
  it("waits for the first word instead of filling the bubble alone", async () => {
    mocks.authenticatedFetch.mockResolvedValue(
      ok([
        'data: {"type":"masthead","text":"🏛️ **Backend Architect**\\n\\n"}\n\n',
        'data: {"type":"text","text":"Start"}\n\n',
        'data: {"type":"done"}\n\n',
      ]),
    );
    await mountAndCapture();
    // one yield, not two: a masthead-only yield is a text part, and a message
    // with a part hides the working indicator for the whole model wait
    expect(await drain()).toEqual(["🏛️ **Backend Architect**\n\nStart"]);
  });

  it("still shows the nameplate when no word follows it", async () => {
    mocks.authenticatedFetch.mockResolvedValue(
      ok([
        'data: {"type":"masthead","text":"🏛️ **Backend Architect**\\n\\n"}\n\n',
        'data: {"type":"done"}\n\n',
      ]),
    );
    await mountAndCapture();
    expect(await drain()).toEqual(["🏛️ **Backend Architect**\n\n"]);
  });
});

describe("the chat SSE reader", () => {
  it("accumulates text events and yields the running transcript", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        ok([
          'data: {"type":"text","text":"Hel"}\n\n',
          'data: {"type":"text","text":"lo"}\n\n',
        ]),
      ),
    );
    await mountAndCapture();
    expect(await drain()).toEqual(["Hel", "Hello"]);
  });

  it("discards already-buffered frames after an identity change", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(ok([
      'data: {"type":"text","text":"first"}\n\ndata: {"type":"text","text":"private tail"}\n\n',
    ])));
    await mountAndCapture();
    const stream = mocks.captured!.run({ messages: [{ content: [{ type: "text", text: "hello" }] }] });
    expect((await stream.next()).value?.content[0].text).toBe("first");
    localStorage.setItem("skein-oidc-generation", "another-session");
    await expect(stream.next()).rejects.toThrow(/identity changed/);
  });

  it("does not care where a network chunk boundary falls", async () => {
    // one event split mid-JSON across two reads: the buffer must hold the
    // partial line instead of parsing and discarding it
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(ok(['data: {"type":"te', 'xt","text":"whole"}\n\n'])),
    );
    await mountAndCapture();
    expect(await drain()).toEqual(["whole"]);
  });

  it("yields a truncated tail when the stream closes without a blank line", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(ok(['data: {"type":"text","text":"cut off"}'])),
    );
    await mountAndCapture();
    expect(await drain()).toEqual(["cut off"]);
  });

  it("tolerates a malformed line instead of ending the stream", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        ok([
          ": keep-alive from a proxy\n\n",
          "data: {not json}\n\n",
          'data: {"type":"text","text":"survived"}\n\n',
        ]),
      ),
    );
    await mountAndCapture();
    expect(await drain()).toEqual(["survived"]);
  });

  it("renders an error event into the transcript rather than throwing", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(ok(['data: {"type":"error","message":"tool blew up"}\n\n'])),
    );
    await mountAndCapture();
    expect((await drain())[0]).toContain("tool blew up");
  });

  it("refreshes the sidebar even when the stream ends early", async () => {
    // the backend keeps the partial exchange, so a stopped stream still
    // changed the thread list
    const seen = vi.fn();
    window.addEventListener("skein-chat-activity", seen);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(ok([])));
    await mountAndCapture();
    await drain();
    expect(seen).toHaveBeenCalled();
    window.removeEventListener("skein-chat-activity", seen);
  });
});

describe("chat request errors", () => {
  it("surfaces the body's message, which carries the usable instruction", async () => {
    // the rate-limit reply names the wait; the status line alone does not
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 429,
        statusText: "Too Many Requests",
        json: async () => ({ detail: "Wait 34 seconds, then send the request again." }),
      }),
    );
    await mountAndCapture();
    await expect(drain()).rejects.toThrow("Wait 34 seconds");
  });

  it("falls back to the status line when the body is not JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 502,
        statusText: "Bad Gateway",
        json: async () => {
          throw new Error("not json");
        },
      }),
    );
    await mountAndCapture();
    await expect(drain()).rejects.toThrow("502 Bad Gateway");
  });

  it.each(["resolve", "abort"])("does not close another identity's chat when an old 404 body can %s", async (outcome) => {
    let session = { authenticated: true, user: "ava", strong: true, auth_method: "api-key", csrf_token: "csrf-ava" };
    let startBody!: () => void;
    const reading = new Promise<void>((resolve) => { startBody = resolve; });
    let finishBody!: () => void;
    const body = new Promise<unknown>((resolve, reject) => {
      finishBody = () => outcome === "resolve"
        ? resolve({ detail: "That chat is not available." })
        : reject(new DOMException("The request was aborted.", "AbortError"));
    });
    const missing = new Response(null, { status: 404 });
    vi.spyOn(missing, "json").mockImplementation(() => { startBody(); return body; });
    vi.stubGlobal("fetch", vi.fn(async (input: string) => {
      if (input.endsWith("/api/chat")) return missing;
      if (input.endsWith("/auth/config"))
        return new Response(JSON.stringify({ mode: "api-key", error: "" }));
      if (input.endsWith("/auth/session/key"))
        session = { ...session, user: "marcus", csrf_token: "csrf-marcus" };
      return new Response(JSON.stringify(session));
    }));
    const auth = await import("@/lib/auth");
    await auth.bootstrapSession(true);
    await mountAndCapture();
    const seen = vi.fn();
    window.addEventListener("skein-chat-missing", seen);
    try {
      const stream = mocks.captured!.run({ messages: [{ content: [{ type: "text", text: "hello" }] }] });
      const pending = stream.next();
      const rejected = expect(pending).rejects.toThrow(/identity changed/);
      await reading;
      await auth.signInWithKey("sk-skein-marcus");
      expect(auth.signedInUser()).toBe("marcus");
      finishBody();
      await rejected;
      expect(seen).not.toHaveBeenCalled();
    } finally {
      window.removeEventListener("skein-chat-missing", seen);
    }
  });

  it("announces a missing chat before it reports that the message was not sent", async () => {
    const seen = vi.fn();
    window.addEventListener("skein-chat-missing", seen);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 404,
        statusText: "Not Found",
        json: async () => ({ detail: "no chat" }),
      }),
    );
    await mountAndCapture();

    await expect(drain()).rejects.toThrow("Message not sent");
    expect((seen.mock.calls[0][0] as CustomEvent).detail).toEqual({ threadId: "t1" });
    window.removeEventListener("skein-chat-missing", seen);
  });
});

describe("hydrating a thread's saved transcript", () => {
  it("gates the thread UI until the transcript has loaded", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(ok([])));
    mocks.chatThreads.mockReturnValue(new Promise(() => {})); // never settles
    render(
      <RuntimeProvider threadId="t1">
        <p>thread ui</p>
      </RuntimeProvider>,
    );
    expect(screen.getByText(/Unrolling the transcript/)).toBeDefined();
    expect(screen.queryByText("thread ui")).toBeNull();
  });

  it("loads a saved thread's messages into the runtime", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(ok([])));
    mocks.chatThreads.mockResolvedValue([{ id: "t1" }]);
    mocks.api.mockResolvedValue({ messages: [{ id: 7, role: "user", content: "earlier", created_at: "2026-09-01T12:00:00Z" }], next_before: null });
    await mountAndCapture();
    await waitFor(() => expect(mocks.thread.reset).toHaveBeenCalled());
    expect(mocks.thread.reset.mock.calls[0][0]).toEqual([
      { id: "saved-7", role: "user", content: [{ type: "text", text: "earlier" }], createdAt: new Date("2026-09-01T12:00:00Z") },
    ]);
  });

  it("never probes messages for a thread that was never saved", async () => {
    // probing a brand-new thread logs a console 404 on every new chat
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(ok([])));
    mocks.chatThreads.mockResolvedValue([{ id: "other" }]);
    await mountAndCapture();
    await waitFor(() => expect(screen.getByText("thread ui")).toBeDefined());
    expect(mocks.api).not.toHaveBeenCalled();
    expect(mocks.thread.reset).not.toHaveBeenCalled();
  });

  it("does not clobber messages that arrived before hydration finished", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(ok([])));
    mocks.chatThreads.mockResolvedValue([{ id: "t1" }]);
    mocks.api.mockResolvedValue({ messages: [{ id: 8, role: "user", content: "stale", created_at: "2026-09-01T12:00:00Z" }], next_before: null });
    mocks.thread.getState.mockReturnValue({ messages: [{ id: "already typed" }] });
    await mountAndCapture();
    await waitFor(() => expect(screen.getByText("thread ui")).toBeDefined());
    expect(mocks.thread.reset).not.toHaveBeenCalled();
  });

  it("reports a failed load instead of rendering history as an empty chat", async () => {
    // swallowed, this showed a saved conversation as blank and the user
    // typed over it believing the thread was new
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(ok([])));
    mocks.chatThreads.mockRejectedValue(new Error("backend unreachable"));
    await mountAndCapture();
    await waitFor(() => expect(mocks.reportStatus).toHaveBeenCalled());
    expect(mocks.reportStatus.mock.calls[0][0]).toContain("saved messages did not load");
    // A retry must not reset a new turn typed over unread saved history.
    expect(screen.queryByText("thread ui")).toBeNull();
    expect(screen.getByRole("button", { name: "Retry saved messages" })).toBeDefined();
  });
});
