import type { Page } from "@playwright/test";

export type ChatRead = {
  status: number;
  done: boolean;
  eof: boolean;
  protocolError: boolean;
  readError: string;
  signalAborted: boolean;
  cancelled: boolean;
};

export type BrowserFailure = {
  kind: "console" | "page" | "request" | "response";
  message?: string;
  method?: string;
  url?: string;
  errorText?: string;
};

export async function observeChat(page: Page, url: string) {
  await page.addInitScript((url: string) => {
    const observations: ChatRead[] = [];
    (window as unknown as { chatReads: ChatRead[] }).chatReads = observations;
    const fetch = window.fetch;
    window.fetch = async function (input, init) {
      if (String(input) !== url || init?.method !== "POST") return fetch.call(this, input, init);
      const state: ChatRead = {
        status: 0, done: false, eof: false, protocolError: false,
        readError: "", signalAborted: !!init.signal?.aborted, cancelled: false,
      };
      observations.push(state);
      init.signal?.addEventListener("abort", () => { state.signalAborted = true; });
      const response = await fetch.call(this, input, init);
      state.status = response.status;
      if (!response.body) return response;
      const getReader = response.body.getReader.bind(response.body);
      // A clone can finish after the application aborts. Observe its own reads,
      // returning the original results without consuming another copy.
      response.body.getReader = (() => {
        const reader = getReader();
        const read = reader.read.bind(reader);
        const cancel = reader.cancel.bind(reader);
        const decoder = new TextDecoder();
        let buffer = "";
        reader.cancel = (reason) => { state.cancelled = true; return cancel(reason); };
        reader.read = async () => {
          try {
            const result = await read();
            buffer += result.done ? decoder.decode() : decoder.decode(result.value, { stream: true });
            const frames = buffer.split("\n\n");
            buffer = frames.pop() ?? "";
            for (const frame of frames) {
              if (!frame.startsWith("data: ")) continue;
              try {
                const event = JSON.parse(frame.slice(6));
                if (event.type === "done") state.done = true;
                if (event.type === "error") state.protocolError = true;
              } catch { /* Keep the adapter's tolerance for non-JSON frames. */ }
            }
            if (result.done) state.eof = true;
            return result;
          } catch (error) {
            state.readError = String(error);
            throw error;
          }
        };
        return reader;
      }) as typeof response.body.getReader;
      return response;
    };
  }, url);
}

export function chatReads(page: Page): Promise<ChatRead[]> {
  return page.evaluate(() => (window as unknown as { chatReads: ChatRead[] }).chatReads);
}

function cleanChatRead(read: ChatRead) {
  return read.status === 200 && read.done && read.eof && !read.protocolError &&
    !read.readError && !read.signalAborted && !read.cancelled;
}

export function remainingFailures(failures: BrowserFailure[], reads: ChatRead[], url: string) {
  // Chromium can report ERR_ABORTED after the application reaches clean EOF.
  // SSE done alone also occurs before truncation, so it cannot excuse a failure.
  return failures.filter((failure) => !(
    failure.kind === "request" && failure.method === "POST" && failure.url === url &&
    failure.errorText === "net::ERR_ABORTED" && reads.length === 1 && cleanChatRead(reads[0])
  ));
}
