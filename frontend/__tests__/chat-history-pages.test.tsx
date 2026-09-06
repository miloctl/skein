import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

class NoopResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal("ResizeObserver", NoopResizeObserver);

const mocks = vi.hoisted(() => ({ api: vi.fn(), chatThreads: vi.fn(), reportStatus: vi.fn(), fetch: vi.fn() }));
vi.mock("@/lib/api", async (original) => ({
  ...await original<typeof import("@/lib/api")>(),
  api: mocks.api,
  authenticatedFetch: mocks.fetch,
}));
vi.mock("@/lib/chat-threads", () => ({ chatThreads: mocks.chatThreads }));
vi.mock("@/lib/status", () => ({ reportStatus: mocks.reportStatus }));

import { RuntimeProvider } from "@/app/runtime-provider";
import { Thread } from "@/components/thread";

const message = (id: number, content = `saved ${id}`) => ({ id, role: id % 2 ? "user" : "assistant", content, created_at: "2026-09-01T12:00:00Z" });
const page = (ids: number[], next_before: number | null) => ({ messages: ids.map((id) => message(id)), next_before });
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const mount = (threadId = "one") => <RuntimeProvider threadId={threadId}><Thread /></RuntimeProvider>;
const older = () => screen.getByRole("button", { name: "Load older messages" });
const rows = () => Array.from(document.querySelectorAll("[data-message-id]")).map((el) => el.textContent);

beforeEach(() => {
  vi.clearAllMocks();
  mocks.chatThreads.mockResolvedValue([{ id: "one" }, { id: "two" }]);
  mocks.api.mockImplementation((path: string) => path.includes("/messages")
    ? Promise.resolve(page([51, 52], 51))
    : Promise.reject(new Error("not used")));
});

it("loads a recent page, prepends in order without replacing nodes or the focused control, and stops at the oldest message", async () => {
  const pending = deferred<ReturnType<typeof page>>();
  mocks.api.mockImplementation((path: string) => path.includes("before=51") ? pending.promise : path.includes("/messages") ? Promise.resolve(page([51, 52], 51)) : Promise.reject(new Error("not used")));
  render(mount());
  await screen.findByText("saved 52");
  expect(mocks.api).toHaveBeenCalledWith("/api/chats/one/messages/page", expect.objectContaining({ cache: "no-store" }));
  const existing = screen.getByText("saved 51");
  const box = screen.getByRole("textbox", { name: /Message/ });
  fireEvent.change(box, { target: { value: "unsent draft" } });
  older().focus();
  fireEvent.click(older());
  fireEvent.click(older());
  expect(screen.getByText("Loading older messages…")).toBeTruthy();
  expect(mocks.api.mock.calls.filter(([path]) => path.includes("before=51"))).toHaveLength(1);
  await act(async () => pending.resolve(page([1, 2], null)));
  expect(rows()).toEqual(["saved 1", "saved 2", "saved 51", "saved 52"]);
  expect(screen.getByText("saved 51")).toBe(existing);
  expect(screen.getByRole("textbox", { name: /Message/ })).toBe(box);
  expect((box as HTMLTextAreaElement).value).toBe("unsent draft");
  expect(document.activeElement).toBe(older());
  expect(screen.getByText("Oldest message reached.")).toBeTruthy();
  expect(older().getAttribute("aria-disabled")).toBe("true");
  expect(mocks.fetch).not.toHaveBeenCalled();
  expect(mocks.reportStatus).not.toHaveBeenCalledWith("Reply complete.", "confirmation");
});

it("restores the visible message anchor after the reader scrolls during an older-page request", async () => {
  const pending = deferred<ReturnType<typeof page>>();
  mocks.api.mockImplementation((path: string) => path.includes("before=51") ? pending.promise
    : path.includes("/messages") ? Promise.resolve(page([51, 52], 51)) : Promise.reject(new Error("not used")));
  render(mount());
  await screen.findByText("saved 52");
  const scroller = document.querySelector<HTMLElement>(".overflow-y-auto")!;
  const row = screen.getByText("saved 51").closest<HTMLElement>("[data-message-id]")!;
  vi.spyOn(scroller, "getBoundingClientRect").mockImplementation(() => ({ top: 100, bottom: 700 }) as DOMRect);
  vi.spyOn(row, "getBoundingClientRect").mockImplementation(() => {
    const top = 400 + (screen.queryByText("saved 49") ? 400 : 0) - scroller.scrollTop;
    return { top, bottom: top + 80 } as DOMRect;
  });
  scroller.scrollTop = 200;
  fireEvent.click(older());
  scroller.scrollTop = 260;
  const box = screen.getByRole("textbox", { name: /Message/ });
  box.focus();
  await act(async () => pending.resolve(page([49, 50], null)));
  expect(scroller.scrollTop).toBe(660);
  expect(row.getBoundingClientRect().top).toBe(140);
  expect(document.activeElement).toBe(box);
});

it("keeps the composer gated after an initial error and opens it only after an explicit retry", async () => {
  mocks.chatThreads.mockRejectedValueOnce(new Error("connection failed"));
  render(mount());
  await screen.findByRole("button", { name: "Retry saved messages" });
  expect(screen.queryByRole("textbox", { name: /Message/ })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Retry saved messages" }));
  await screen.findByText("saved 52");
  expect(screen.getByRole("textbox", { name: /Message/ })).toBeTruthy();
});

it("offers retry when the saved-thread lookup times out and ignores its late result", async () => {
  vi.useFakeTimers();
  try {
    const pending = deferred<{ id: string }[]>();
    mocks.chatThreads.mockReturnValue(pending.promise);
    render(mount());
    await act(async () => vi.advanceTimersByTimeAsync(15_000));
    expect(screen.getByText(/Saved messages did not load in time/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Retry saved messages" })).toBeTruthy();
    await act(async () => pending.resolve([{ id: "one" }]));
    expect(screen.queryByText("saved 52")).toBeNull();
    expect(screen.queryByRole("textbox", { name: /Message/ })).toBeNull();
  } finally { vi.useRealTimers(); }
});

it("reports an older-page timeout without browser abort wording and retries its cursor", async () => {
  let attempts = 0;
  mocks.api.mockImplementation((path: string, init?: RequestInit) => {
    if (path.includes("before=51")) {
      if (++attempts > 1) return Promise.resolve(page([49, 50], null));
      return new Promise((_resolve, reject) => {
        init!.signal!.addEventListener("abort", () => reject(init!.signal!.reason), { once: true });
      });
    }
    return path.includes("/messages") ? Promise.resolve(page([51, 52], 51)) : Promise.reject(new Error("not used"));
  });
  render(mount());
  await screen.findByText("saved 52");
  vi.useFakeTimers();
  try {
    fireEvent.click(older());
    await act(async () => vi.advanceTimersByTimeAsync(15_000));
    expect(screen.getByRole("alert").textContent).toBe("Older messages did not load in time. Select Load older messages to try again.");
    expect(screen.getByText("saved 52")).toBeTruthy();
    fireEvent.click(older());
    await act(async () => Promise.resolve());
    expect(screen.getByText("saved 49")).toBeTruthy();
    expect(attempts).toBe(2);
  } finally { vi.useRealTimers(); }
});

it("retains history on a load error and retries the same cursor", async () => {
  let attempts = 0;
  mocks.api.mockImplementation((path: string) => path.includes("before=51")
    ? (++attempts === 1 ? Promise.reject(new Error("try again")) : Promise.resolve(page([49, 50], null)))
    : path.includes("/messages") ? Promise.resolve(page([51, 52], 51)) : Promise.reject(new Error("not used")));
  render(mount());
  await screen.findByText("saved 52");
  fireEvent.click(older());
  await screen.findByText(/Older messages did not load/);
  expect(screen.getByText("saved 51")).toBeTruthy();
  fireEvent.click(older());
  await screen.findByText("saved 49");
  expect(attempts).toBe(2);
  expect(rows()).toEqual(["saved 49", "saved 50", "saved 51", "saved 52"]);
});

it("discards an older page and its metadata after switching threads without relying on the caller key", async () => {
  const pending = deferred<ReturnType<typeof page>>();
  mocks.api.mockImplementation((path: string) => path.includes("before=51") ? pending.promise
    : path.includes("/two/messages") ? Promise.resolve(page([91, 92], null))
      : path.includes("/messages") ? Promise.resolve(page([51, 52], 51)) : Promise.reject(new Error("not used")));
  const view = render(mount());
  await screen.findByText("saved 52");
  fireEvent.click(older());
  view.rerender(mount("two"));
  expect(screen.queryByText("saved 52")).toBeNull();
  await screen.findByText("saved 92");
  await act(async () => pending.resolve(page([1, 2], 1)));
  expect(rows()).toEqual(["saved 91", "saved 92"]);
  expect(screen.getByText("Oldest message reached.")).toBeTruthy();
});

it("discards late history and clears the rendered transcript when the browser identity changes", async () => {
  const pending = deferred<ReturnType<typeof page>>();
  mocks.api.mockImplementation((path: string) => path.includes("before=51") ? pending.promise
    : path.includes("/messages") ? Promise.resolve(page([51, 52], 51)) : Promise.reject(new Error("not used")));
  render(mount());
  await screen.findByText("saved 52");
  fireEvent.click(older());
  mocks.chatThreads.mockResolvedValue([]);
  act(() => {
    localStorage.setItem("skein-oidc-generation", "different-identity");
    window.dispatchEvent(new Event("storage"));
  });
  expect(screen.queryByText("saved 52")).toBeNull();
  await act(async () => pending.resolve(page([1, 2], 1)));
  expect(screen.queryByText("saved 1")).toBeNull();
  expect(screen.queryByRole("button", { name: "Load older messages" })).toBeNull();
});

it("keeps a concurrently streamed new turn after prepending history", async () => {
  const pending = deferred<ReturnType<typeof page>>();
  const encoder = new TextEncoder();
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  mocks.fetch.mockResolvedValue({ ok: true, body: new ReadableStream<Uint8Array>({ start(value) { controller = value; } }) });
  mocks.api.mockImplementation((path: string) => path.includes("before=51") ? pending.promise
    : path.includes("/messages") ? Promise.resolve(page([51, 52], 51)) : Promise.reject(new Error("not used")));
  render(mount());
  await screen.findByText("saved 52");
  fireEvent.click(older());
  const box = screen.getByRole("textbox", { name: /Message/ });
  fireEvent.change(box, { target: { value: "new turn" } });
  fireEvent.keyDown(box, { key: "Enter" });
  await waitFor(() => expect(mocks.fetch).toHaveBeenCalledTimes(1));
  await act(async () => controller.enqueue(encoder.encode('data: {"type":"text","text":"new answer"}\n\n')));
  const answer = await screen.findByText("new answer");
  await act(async () => pending.resolve(page([49, 50], null)));
  expect(screen.getByText("new answer")).toBe(answer);
  await act(async () => controller.enqueue(encoder.encode('data: {"type":"text","text":" continued"}\n\n')));
  await screen.findByText("new answer continued");
  await act(async () => controller.close());
  await waitFor(() => expect(rows()).toEqual(["saved 49", "saved 50", "saved 51", "saved 52", "new turn", "new answer continued"]));
  expect(mocks.fetch).toHaveBeenCalledTimes(1);
});
