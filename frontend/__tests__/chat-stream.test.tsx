import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** The chat adapter is the one write surface that does NOT go through
 *  api(), so every behavior api() gives the rest of the app has to be
 *  rebuilt here — and has drifted twice already (the bearer() ladder, the
 *  401 session handling; both are recorded in app/runtime-provider.tsx).
 *  These pin the rebuilt copies and the SSE reader.
 *
 *  The adapter is module-private, so the test captures it where the runtime
 *  library receives it. Everything below the capture is the real code. */

const mocks = vi.hoisted(() => ({
  bearer: vi.fn(),
  accessTokenSync: vi.fn(),
  sessionRejected: vi.fn(),
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
  useThreadRuntime: () => mocks.thread,
}));

vi.mock("@/lib/api", () => ({
  API_URL: "http://backend.test",
  api: mocks.api,
  bearer: mocks.bearer,
  userHeader: () => ({ "X-User": "tester" }),
  actionError: (e: unknown) => (e as Error).message,
}));
vi.mock("@/lib/auth", () => ({
  accessTokenSync: mocks.accessTokenSync,
  sessionRejected: mocks.sessionRejected,
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
  mocks.bearer.mockResolvedValue("tok");
  mocks.accessTokenSync.mockReturnValue("tok");
  mocks.chatThreads.mockResolvedValue([]);
  mocks.thread.getState.mockReturnValue({ messages: [] });
  mocks.captured = null;
});

afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
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

describe("the chat request carries the shared credential", () => {
  it("sends the bearer() ladder's token, not a rebuilt one", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok([]));
    vi.stubGlobal("fetch", fetchMock);
    mocks.bearer.mockResolvedValue("ladder-token");
    await mountAndCapture();
    await drain();
    const headers = fetchMock.mock.calls[0][1].headers;
    expect(headers.Authorization).toBe("Bearer ladder-token");
    expect(headers["X-User"]).toBe("tester");
  });

  it("ends the session when the backend rejects that exact token", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        statusText: "Unauthorized",
        json: async () => ({ detail: "token expired" }),
      }),
    );
    await mountAndCapture();
    await expect(drain()).rejects.toThrow("token expired");
    expect(mocks.sessionRejected).toHaveBeenCalledWith("tok");
  });

  it("leaves the session alone when the 401 is not about the signed-in token", async () => {
    // an api-key 401 must not sign an OIDC user out
    mocks.bearer.mockResolvedValue("some-api-key");
    mocks.accessTokenSync.mockReturnValue("a-different-oidc-token");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        statusText: "Unauthorized",
        json: async () => ({ detail: "bad key" }),
      }),
    );
    await mountAndCapture();
    await expect(drain()).rejects.toThrow("bad key");
    expect(mocks.sessionRejected).not.toHaveBeenCalled();
  });

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
    mocks.api.mockResolvedValue([{ role: "user", content: "earlier" }]);
    await mountAndCapture();
    await waitFor(() => expect(mocks.thread.reset).toHaveBeenCalled());
    expect(mocks.thread.reset.mock.calls[0][0]).toEqual([
      { role: "user", content: [{ type: "text", text: "earlier" }] },
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
    mocks.api.mockResolvedValue([{ role: "user", content: "stale" }]);
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
    // and the UI still opens, rather than hanging on the loading line
    expect(screen.getByText("thread ui")).toBeDefined();
  });
});
