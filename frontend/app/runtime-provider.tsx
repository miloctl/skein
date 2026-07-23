"use client";

import { useMemo, useState, type ReactNode } from "react";
import {
  AssistantRuntimeProvider,
  useLocalRuntime,
  type ChatModelAdapter,
} from "@assistant-ui/react";

import { API_URL, getUser } from "@/lib/api";

/** Streams from the FastAPI backend, which emits SSE lines of
 *  {"type": "text" | "tool" | "error" | "done", ...}.
 *
 *  The thread id is minted per runtime mount so the visible UI history and
 *  the backend session always agree (navigating away starts a fresh thread).
 *  TODO: add a backend chat-history endpoint, then persist the thread id and
 *  hydrate past messages on mount instead. */
function makeAdapter(threadId: string): ChatModelAdapter {
  return {
    async *run({ messages, abortSignal }) {
      const last = messages[messages.length - 1];
      const text = last.content
        .filter((p) => p.type === "text")
        .map((p) => p.text)
        .join("\n");

      const token = process.env.NEXT_PUBLIC_API_TOKEN;
      const res = await fetch(`${API_URL}/api/chat`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-User": getUser(),
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ thread_id: threadId, message: text }),
        signal: abortSignal,
      });
      if (!res.ok || !res.body) {
        throw new Error(`Backend error: ${res.status} ${res.statusText}`);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let acc = "";

      const handle = (chunk: string): string | null => {
        if (!chunk.startsWith("data: ")) return null;
        let event;
        try {
          event = JSON.parse(chunk.slice(6));
        } catch {
          return null; // tolerate malformed lines (e.g. proxy keep-alives)
        }
        if (event.type === "text") acc += event.text;
        else if (event.type === "tool") acc += `\n\n*🔧 ${event.name}…*\n\n`;
        else if (event.type === "error") acc += `\n\n> ⚠️ ${event.message}\n`;
        else return null;
        return acc;
      };

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const chunks = buffer.split("\n\n");
        buffer = chunks.pop() ?? "";
        for (const chunk of chunks) {
          if (handle(chunk) !== null) {
            yield { content: [{ type: "text", text: acc }] };
          }
        }
      }
      buffer += decoder.decode(); // flush a truncated tail on abrupt close
      if (buffer && handle(buffer) !== null) {
        yield { content: [{ type: "text", text: acc }] };
      }
    },
  };
}

export function RuntimeProvider({ children }: { children: ReactNode }) {
  const [threadId] = useState(() => crypto.randomUUID());
  const adapter = useMemo(() => makeAdapter(threadId), [threadId]);
  const runtime = useLocalRuntime(adapter);
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      {children}
    </AssistantRuntimeProvider>
  );
}
