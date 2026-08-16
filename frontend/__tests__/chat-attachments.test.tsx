import { render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** The attachment half of the chat adapter. Both adapters are module-private,
 *  so the test captures them where the runtime library receives them — the
 *  same shape __tests__/chat-stream.test.tsx uses, and everything below the
 *  capture is the real code. */

const mocks = vi.hoisted(() => ({
  bearer: vi.fn(),
  chatThreads: vi.fn(),
  api: vi.fn(),
  thread: {
    getState: vi.fn(() => ({ messages: [] as unknown[] })),
    reset: vi.fn(),
  },
  captured: null as null | {
    chat: {
      run: (o: {
        messages: { content: unknown[]; attachments?: { id: string }[] }[];
      }) => AsyncGenerator<unknown>;
    };
    attachments: {
      accept: string;
      add: (s: { file: File }) => Promise<{ id: string; name: string }>;
      send: (a: unknown) => Promise<{ id: string; name: string }>;
    };
  },
}));

vi.mock("@assistant-ui/react", () => ({
  AssistantRuntimeProvider: ({ children }: { children: React.ReactNode }) => (
    <>{children}</>
  ),
  useLocalRuntime: (chat: never, options: never) => {
    mocks.captured = {
      chat,
      attachments: (options as { adapters: { attachments: never } }).adapters
        .attachments,
    };
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
  accessTokenSync: () => "",
  sessionRejected: vi.fn(),
}));
vi.mock("@/lib/status", () => ({ reportStatus: vi.fn() }));
vi.mock("@/lib/chat-threads", () => ({ chatThreads: mocks.chatThreads }));
vi.mock("@/lib/persona", () => ({ outgoing: (t: string) => t }));

import { RuntimeProvider } from "@/app/runtime-provider";

function adapters() {
  mocks.chatThreads.mockResolvedValue([]);
  render(<RuntimeProvider threadId="t1">{null}</RuntimeProvider>);
  if (!mocks.captured) throw new Error("adapters were not captured");
  return mocks.captured;
}

beforeEach(() => {
  mocks.bearer.mockResolvedValue("");
  mocks.captured = null;
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("attaching a file", () => {
  it("stores nothing until the message is sent", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const { attachments } = adapters();
    const pending = await attachments.add({
      file: new File(["x"], "notes.md", { type: "text/markdown" }),
    });
    // add() only stages it: a file uploaded on pick would leave a stored row
    // behind for every attachment a person reconsiders
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(pending.name).toBe("notes.md");
  });

  it("uploads on send and keeps the id the server gave it", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ id: 42, title: "notes.md" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const { attachments } = adapters();
    const file = new File(["x"], "notes.md", { type: "text/markdown" });
    const sent = await attachments.send({ file, name: "notes.md" });
    expect(sent.id).toBe("42");
    expect(sent.name).toBe("notes.md");
  });

  it("keeps the draft when the upload is refused", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "that file type cannot be attached." }), {
        status: 400,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const { attachments } = adapters();
    const file = new File(["x"], "payload.svg", { type: "image/svg+xml" });
    // send() rejecting is what makes assistant-ui hold the composer's text,
    // so a refused attachment never sends the message without it
    await expect(attachments.send({ file, name: "payload.svg" })).rejects.toThrow(
      /cannot be attached/,
    );
  });

  it("sends the ids to the chat route, not the file content", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("", { status: 500 }),
    );
    const { chat } = adapters();
    const run = chat.run({
      messages: [
        {
          content: [{ type: "text", text: "what does it say?" }],
          attachments: [{ id: "42" }, { id: "43" }],
        },
      ],
    });
    await run.next().catch(() => undefined);
    const body = JSON.parse(
      (fetchSpy.mock.calls[0][1] as { body: string }).body,
    );
    expect(body.attachments).toEqual([42, 43]);
    expect(body.message).toBe("what does it say?");
  });

  it("drops an attachment id that never became a stored file", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("", { status: 500 }),
    );
    const { chat } = adapters();
    const run = chat.run({
      messages: [
        {
          content: [{ type: "text", text: "hi" }],
          // the id add() stages before send() replaces it — a request carrying
          // it would be a 422 the person cannot act on
          attachments: [{ id: "pending-notes.md-1" }],
        },
      ],
    });
    await run.next().catch(() => undefined);
    const body = JSON.parse(
      (fetchSpy.mock.calls[0][1] as { body: string }).body,
    );
    expect(body.attachments).toEqual([]);
  });
});
