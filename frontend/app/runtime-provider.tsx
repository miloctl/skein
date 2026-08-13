"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  AssistantRuntimeProvider,
  useLocalRuntime,
  useThreadRuntime,
  type ChatModelAdapter,
  type ThreadMessageLike,
} from "@assistant-ui/react";

import { API_URL, actionError, api, bearer, userHeader } from "@/lib/api";
import { accessTokenSync, sessionRejected } from "@/lib/auth";
import { reportStatus } from "@/lib/status";
import { chatThreads } from "@/lib/chat-threads";
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

      // bearer() itself, not a copy of its ladder: this call site rebuilt the
      // ladder and lost the OIDC rung, so in oidc mode a signed-in user with
      // no personal key got a 401 telling them to sign in. Chat is the one
      // surface that does not go through api(), which is why it drifted.
      const auth = await bearer();
      const res = await fetch(`${API_URL}/api/chat`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...userHeader(),
          ...(auth ? { Authorization: `Bearer ${auth}` } : {}),
        },
        body: JSON.stringify({ thread_id: threadId, message: text }),
        signal: abortSignal,
      });
      if (!res.ok || !res.body) {
        // chat does not go through api(), so it is also the one 401 that
        // never reached the session handling there. A person whose only
        // activity is chatting kept a signed-in UI until some other surface
        // happened to fetch. Same drift the bearer() comment above records.
        if (res.status === 401 && auth && auth === accessTokenSync()) sessionRejected(auth);
        // the body carries the usable message ("The limit for chat is 20 per
        // minute per person. Wait 34 seconds, then send the request again.",
        // length caps) — surface it, not just the code
        let detail = "";
        try {
          const parsed = await res.json();
          detail = typeof parsed.detail === "string" ? parsed.detail : JSON.stringify(parsed.detail);
        } catch {
          /* non-JSON body: fall through to the status line */
        }
        throw new Error(detail || `Backend error: ${res.status} ${res.statusText}`);
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
        else if (event.type === "receipt") acc += receiptLine(event);
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
    // the shared list answers "is this thread saved?" from cache — probing
    // /messages for a brand-new thread logs a console 404 on every new chat
    chatThreads()
      .then((rows) =>
        rows.some((t) => t.id === threadId)
          ? api<StoredMessage[]>(`/api/chats/${threadId}/messages`)
          : ([] as StoredMessage[]),
      )
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
      .catch((e) => {
        // the brand-new-thread case RESOLVES [] above, so a rejection here is
        // a real failure — the saved list or this thread's messages did not
        // load. Swallowed, it rendered saved history as an empty conversation
        // the user types over believing the thread is new.
        if (!cancelled) reportStatus(`This chat's saved messages did not load. ${actionError(e)}`);
      })
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

/** A write receipt states what actually happened to your data — the gate
 *  reports it, so it is a fact rather than something the model claimed.
 *  `actor` arrives only when the server decided it says something new
 *  (routes/chat.py::_attributed) — a consulted specialist's write in the
 *  orchestrator's turn. Exported for the pairing test that keeps this
 *  renderer and the stored transcript (chat.py::_receipt_line) telling the
 *  same story per kind. */
export function receiptLine(e: {
  kind: string;
  entity: string;
  detail: string;
  ref: number;
  actor?: string;
}): string {
  const ref = e.ref ? ` #${e.ref}` : "";
  const actor = e.actor ? ` (${e.actor})` : "";
  const head =
    e.kind === "queued"
      ? `🕓 **Queued for review** — ${e.entity}${ref}${actor} needs a human verdict`
      : e.kind === "wrote"
        ? `✅ **Wrote ${e.entity}${ref}${actor}**`
        : e.kind === "refused"
          ? // the sentence slot, not a suffix: "this agent" in a consult
            // claims the wrong refusee — the gate refused the SPECIALIST
            `⛔ **Refused** — ${e.entity} is forbidden for ${e.actor || "this agent"}`
          : e.kind === "nothing"
            ? `📭 **Filed nothing**`
            : e.kind === "unnotified"
              ? `🔕 **Not notified** — ${e.entity}`
              : `⚠️ **Not written** — ${e.entity}${actor}`;
  const tail = e.detail ? `: ${e.detail}` : "";
  const link =
    e.kind === "queued" && e.ref ? ` · [open in Inbox](/review)` : "";
  return `\n\n> ${head}${tail}${link}\n\n`;
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
