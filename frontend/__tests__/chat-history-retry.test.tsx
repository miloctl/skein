import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { RuntimeProvider } from "@/app/runtime-provider";
import { bootstrapSession } from "@/lib/auth";
import { chatThreads } from "@/lib/chat-threads";

const json = (body: unknown) => new Response(JSON.stringify(body));
const session = { authenticated: false, user: "anonymous", strong: false, auth_method: "", csrf_token: "" };

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  window.dispatchEvent(new Event("storage"));
});

it("reports its own request timeout in plain words and preserves other transport errors", async () => {
  const transportError = new TypeError("Failed to fetch");
  let timedOut = false;
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    if (input.endsWith("/auth/config")) return json({ mode: "trusted-header", error: "" });
    if (input.endsWith("/auth/session")) return json(session);
    if (timedOut) throw transportError;
    return new Promise<Response>((_resolve, reject) => {
      init!.signal!.addEventListener("abort", () => {
        timedOut = true;
        reject(init!.signal!.reason);
      }, { once: true });
    });
  }));
  await bootstrapSession(true);
  vi.useFakeTimers();
  const result = chatThreads().catch((error: Error) => error);
  await vi.advanceTimersByTimeAsync(15_000);
  expect(await result).toEqual(new Error("The saved chats did not load in time. Try again."));
  await expect(chatThreads()).rejects.toBe(transportError);
});

it("retries both real cache layers after a hung shared list lookup without letting its late response replace the fresh cache", async () => {
  let lookups = 0;
  let release!: (response: Response) => void;
  let firstSignal: AbortSignal | undefined;
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    if (input.endsWith("/auth/config")) return json({ mode: "trusted-header", error: "" });
    if (input.endsWith("/auth/session")) return json(session);
    if (input.endsWith("/api/chats")) {
      lookups++;
      if (lookups === 1) {
        firstSignal = init?.signal ?? undefined;
        // Resolve late even after abort, as a response/body continuation can.
        return new Promise<Response>((resolve) => { release = resolve; });
      }
      return json([{ id: "one" }]);
    }
    if (input.endsWith("/messages/page")) return json({ messages: [], next_before: null });
    throw new Error(`Unexpected request: ${input}`);
  }));
  await bootstrapSession(true);
  vi.useFakeTimers();
  render(<RuntimeProvider threadId="one"><p>ready</p></RuntimeProvider>);
  await act(async () => Promise.resolve());
  const shared = chatThreads();
  expect(chatThreads()).toBe(shared);
  expect(lookups).toBe(1);
  await act(async () => vi.advanceTimersByTimeAsync(15_000));
  expect(screen.getByText(/Saved messages did not load in time/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Retry saved messages" }));
  await act(async () => Promise.resolve());
  expect(lookups).toBe(2);
  expect(firstSignal?.aborted).toBe(true);
  expect(screen.getByText("ready")).toBeTruthy();
  const fresh = chatThreads();
  expect(await fresh).toEqual([{ id: "one" }]);
  await act(async () => { release(json([{ id: "old" }])); await shared; });
  expect(chatThreads()).toBe(fresh);
  expect(await chatThreads()).toEqual([{ id: "one" }]);
  expect(lookups).toBe(2);
});
