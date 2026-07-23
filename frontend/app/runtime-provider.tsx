"use client";

import type { ReactNode } from "react";
import {
  AssistantRuntimeProvider,
  useLocalRuntime,
  type ChatModelAdapter,
} from "@assistant-ui/react";

import { API_URL, getThreadId, getUser } from "@/lib/api";

/** Streams from the FastAPI backend, which emits SSE lines of
 *  {"type": "text" | "tool" | "error" | "done", ...}. */
const backendAdapter: ChatModelAdapter = {
  async *run({ messages, abortSignal }) {
    const last = messages[messages.length - 1];
    const text = last.content
      .filter((p) => p.type === "text")
      .map((p) => p.text)
      .join("\n");

    const res = await fetch(`${API_URL}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User": getUser() },
      body: JSON.stringify({ thread_id: getThreadId(), message: text }),
      signal: abortSignal,
    });
    if (!res.ok || !res.body) {
      throw new Error(`Backend error: ${res.status} ${res.statusText}`);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let acc = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const chunks = buffer.split("\n\n");
      buffer = chunks.pop() ?? "";
      for (const chunk of chunks) {
        if (!chunk.startsWith("data: ")) continue;
        const event = JSON.parse(chunk.slice(6));
        if (event.type === "text") {
          acc += event.text;
        } else if (event.type === "tool") {
          acc += `\n\n*🔧 ${event.name}…*\n\n`;
        } else if (event.type === "error") {
          acc += `\n\n> ⚠️ ${event.message}\n`;
        }
        yield { content: [{ type: "text", text: acc }] };
      }
    }
  },
};

export function RuntimeProvider({ children }: { children: ReactNode }) {
  const runtime = useLocalRuntime(backendAdapter);
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      {children}
    </AssistantRuntimeProvider>
  );
}
