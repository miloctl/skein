"use client";

import { useEffect, useMemo, type ReactNode } from "react";
import {
  AssistantRuntimeProvider,
  useLocalRuntime,
  useThreadRuntime,
  type ChatModelAdapter,
  type ThreadMessageLike,
} from "@assistant-ui/react";

import { API_URL, api, getUser } from "@/lib/api";
import { outgoing } from "@/lib/persona";

/** Streams from the FastAPI backend, which emits SSE lines of
 *  {"type": "text" | "tool" | "error" | "done", ...}.
 *
 *  The thread id is owned by the chat page (sidebar); the backend logs a
 *  provider-agnostic transcript per thread, which ThreadHydrator loads on
 *  mount so switching chats restores history. */
function makeAdapter(threadId: string): ChatModelAdapter {
  return {
    async *run({ messages, abortSignal }) {
      const last = messages[messages.length - 1];
      // sticky persona: freeform text is invisibly prefixed with /as <slug>
      const text = outgoing(
        last.content
          .filter((p) => p.type === "text")
          .map((p) => p.text)
          .join("\n"),
      );

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
      // the sidebar refreshes titles/ordering after every exchange
      window.dispatchEvent(new Event("skein-chat-activity"));
    },
  };
}

type StoredMessage = { role: "user" | "assistant"; content: string };

/** Loads the stored transcript into the (fresh) runtime on mount. */
function ThreadHydrator({ threadId }: { threadId: string }) {
  const thread = useThreadRuntime();
  useEffect(() => {
    api<StoredMessage[]>(`/api/chats/${threadId}/messages`)
      .then((msgs) => {
        if (msgs.length === 0) return;
        const initial: ThreadMessageLike[] = msgs.map((m) => ({
          role: m.role,
          content: [{ type: "text", text: m.content }],
        }));
        thread.reset(initial);
      })
      .catch(() => {}); // brand-new thread: nothing stored yet
  }, [thread, threadId]);
  return null;
}

export function RuntimeProvider({
  threadId,
  children,
}: {
  threadId: string;
  children: ReactNode;
}) {
  const adapter = useMemo(() => makeAdapter(threadId), [threadId]);
  const runtime = useLocalRuntime(adapter);
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ThreadHydrator threadId={threadId} />
      {children}
    </AssistantRuntimeProvider>
  );
}
