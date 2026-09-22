import { afterEach, describe, expect, it, vi } from "vitest";
import { observeChat, remainingFailures, type BrowserFailure, type ChatRead } from "../e2e-oidc/chat-stream-observer";

const url = "https://127.0.0.1:8601/api/chat";
const complete: ChatRead = {
  status: 200, done: true, eof: true, protocolError: false,
  readError: "", signalAborted: false, cancelled: false,
};
const aborted: BrowserFailure = {
  kind: "request", method: "POST", url, errorText: "net::ERR_ABORTED",
};

afterEach(() => vi.unstubAllGlobals());

async function observedStream() {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const response = new Response(new ReadableStream<Uint8Array>({
    start(value) { controller = value; },
  }));
  vi.stubGlobal("fetch", vi.fn(async () => response));
  const page = {
    addInitScript: async (script: (url: string) => void, arg: string) => script(arg),
  } as unknown as Parameters<typeof observeChat>[0];
  await observeChat(page, url);
  const signal = new AbortController();
  const fetched = await window.fetch(url, { method: "POST", signal: signal.signal });
  const state = (window as unknown as { chatReads: ChatRead[] }).chatReads[0];
  return { controller, reader: fetched.body!.getReader(), signal, state };
}

it("observes split completion frames without substituting them for actual EOF", async () => {
  const { controller, reader, state } = await observedStream();
  const prefix = new TextEncoder().encode('data: {"type":"do');
  controller.enqueue(prefix);
  expect((await reader.read()).value).toBe(prefix);
  expect(state.done).toBe(false);
  controller.enqueue(new TextEncoder().encode('ne"}\n\n'));
  await reader.read();
  expect(state.done).toBe(true);
  expect(state.eof).toBe(false);
  const last = reader.read();
  expect(remainingFailures([aborted], [state], url)).toEqual([aborted]);
  controller.close();
  await expect(last).resolves.toEqual({ done: true, value: undefined });
  expect(remainingFailures([aborted], [state], url)).toEqual([]);
});

it.each(["text", "done"])("preserves reader rejection after a %s frame", async (type) => {
  const { controller, reader, state } = await observedStream();
  controller.enqueue(new TextEncoder().encode(`data: {"type":"${type}"}\n\n`));
  await reader.read();
  controller.error(new TypeError("network error"));
  await expect(reader.read()).rejects.toThrow("network error");
  expect(state.eof).toBe(false);
  expect(state.readError).toBe("TypeError: network error");
  expect(remainingFailures([aborted], [state], url)).toEqual([aborted]);
});

it.each(["signal", "reader"])("records %s cancellation even with a completion frame", async (kind) => {
  const { controller, reader, signal, state } = await observedStream();
  controller.enqueue(new TextEncoder().encode('data: {"type":"done"}\n\n'));
  await reader.read();
  if (kind === "signal") {
    signal.abort();
    controller.close();
  } else {
    await reader.cancel();
  }
  await reader.read();
  expect(state.signalAborted || state.cancelled).toBe(true);
  expect(remainingFailures([aborted], [state], url)).toEqual([aborted]);
});

describe("completed chat request failures", () => {
  it("accepts only the observed clean-EOF reporting event", () => {
    expect(remainingFailures([aborted], [complete], url)).toEqual([]);
  });

  it.each<Partial<ChatRead>>([
    { status: 503 },
    { done: false },
    { eof: false },
    { done: false, eof: false, readError: "TypeError: network error" },
    { eof: false, readError: "TypeError: network error" },
    { protocolError: true },
    { readError: "TypeError: network error" },
    { signalAborted: true },
    { cancelled: true },
  ])("keeps the failure when consumption is not clean: %j", (state) => {
    expect(remainingFailures([aborted], [{ ...complete, ...state }], url)).toEqual([aborted]);
  });

  it("refuses missing or ambiguous observations", () => {
    expect(remainingFailures([aborted], [], url)).toEqual([aborted]);
    expect(remainingFailures([aborted], [complete, complete], url)).toEqual([aborted]);
  });

  it("keeps other request, response, console, and page failures", () => {
    const failures: BrowserFailure[] = [
      { ...aborted, errorText: "net::ERR_INCOMPLETE_CHUNKED_ENCODING" },
      { ...aborted, url: `${url}/specialists` },
      { ...aborted, method: "GET" },
      { ...aborted, errorText: undefined },
      { kind: "response", message: `response: 503 ${url}` },
      { kind: "console", message: "Failed to fetch" },
      { kind: "page", message: "network error" },
    ];
    expect(remainingFailures([aborted, ...failures], [complete], url)).toEqual(failures);
  });
});
