"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  AssistantRuntimeProvider,
  useLocalRuntime,
  useThreadRuntime,
  type ChatModelAdapter,
  type ThreadMessageLike,
} from "@assistant-ui/react";

import { API_URL, api, getApiKey, getUser } from "@/lib/api";
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

      // same auth ladder as lib/api.ts: personal key first, then the
      // deployment token — chat was the one surface that skipped the key
      const auth = getApiKey() || process.env.NEXT_PUBLIC_API_TOKEN;
      const res = await fetch(`${API_URL}/api/chat`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-User": getUser(),
          ...(auth ? { Authorization: `Bearer ${auth}` } : {}),
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

      try {
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
      } finally {
        // sidebar refresh even when the stream was stopped/aborted — the
        // backend keeps the partial exchange, so the list must update too
        window.dispatchEvent(new Event("skein-chat-activity"));
      }
    },
  };
}

type StoredMessage = { role: "user" | "assistant"; content: string };

/** Loads the stored transcript into the (fresh) runtime, then reveals the
 *  thread UI — gating children prevents both the empty-state flash and the
 *  send-before-hydration race (reset() would clobber an in-flight run). */
function ThreadHydrator({
  threadId,
  children,
}: {
  threadId: string;
  children: ReactNode;
}) {
  const thread = useThreadRuntime();
  const [ready, setReady] = useState(false);
  useEffect(() => {
    let cancelled = false;
    api<StoredMessage[]>(`/api/chats/${threadId}/messages`)
      .then((msgs) => {
        if (cancelled) return;
        // never clobber messages that already exist (e.g. a fast send)
        if (msgs.length > 0 && thread.getState().messages.length === 0) {
          const initial: ThreadMessageLike[] = msgs.map((m) => ({
            role: m.role,
            content: [{ type: "text", text: m.content }],
          }));
          thread.reset(initial);
        }
      })
      .catch(() => {}) // brand-new thread: nothing stored yet
      .finally(() => {
        if (!cancelled) setReady(true);
      });
    return () => {
      cancelled = true;
    };
  }, [thread, threadId]);
  if (!ready)
    return <p className="p-8 text-sm text-ink-3">Unrolling the transcript…</p>;
  return <>{children}</>;
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
      <ThreadHydrator threadId={threadId}>{children}</ThreadHydrator>
    </AssistantRuntimeProvider>
  );
}
