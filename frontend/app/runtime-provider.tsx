"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  AssistantRuntimeProvider,
  useLocalRuntime,
  useThreadRuntime,
  type AttachmentAdapter,
  type ChatModelAdapter,
  type ThreadMessageLike,
} from "@assistant-ui/react";

import { API_URL, actionError, api, bearer, userHeader } from "@/lib/api";
import { accessTokenSync, sessionRejected } from "@/lib/auth";
import { reportStatus } from "@/lib/status";
import { chatThreads } from "@/lib/chat-threads";
import { outgoing } from "@/lib/persona";

/** What POST /api/files accepts (backend services/uploads.py). Kept as
 *  extensions rather than MIME types because that is what the backend keys
 *  on, and a browser reports an empty `type` for several of these. */
const ACCEPT =
  ".pdf,.csv,.doc,.docx,.xls,.xlsx,.html,.txt,.md,.png,.jpg,.jpeg,.gif,.webp";

// routes/chat.py::ChatRequest.attachments carries the same cap
const MAX_ATTACHMENTS = 5;

/** Uploads the file when the message is SENT, not when it is picked.
 *
 *  Picking would leave a stored file behind for every attachment a person
 *  reconsiders, and remove() would need a delete endpoint to clean up — one
 *  that does not exist, because deleting a stored file is a destructive write
 *  that belongs behind human review.
 *
 *  The artifact id rides in the attachment's own `id`, which is the field the
 *  adapter owns (SimpleImageAttachmentAdapter fills it with a generated one).
 *  `content` stays empty on purpose: the file goes to the model as a content
 *  block the BACKEND builds from the id, so the bytes never travel through
 *  the browser's message state or the stored transcript. */
function makeAttachmentAdapter(): AttachmentAdapter {
  // add() runs per picked file and send() per sent one, so the composer's own
  // count is not visible here — this tracks what has been staged since the
  // adapter was built (one per thread, RuntimeProvider's useMemo).
  let count = 0;
  return {
    accept: ACCEPT,
    async add({ file }) {
      if (count >= MAX_ATTACHMENTS) {
        // refused HERE, not by the backend: ChatRequest caps attachments at 5,
        // and a sixth would upload (spending quota), then 422 the whole turn
        // with the pydantic detail lost
        const refused = `Attach ${MAX_ATTACHMENTS} files at most in one message.`;
        reportStatus(refused);
        throw new Error(refused);
      }
      count += 1;
      return {
        // randomUUID, not name+size: the composer upserts by id, so two files
        // that share a name and a size (two data.csv exports) collapsed into
        // one and the first was silently dropped from the send
        id: `pending-${crypto.randomUUID()}`,
        type: "document",
        name: file.name,
        contentType: file.type,
        file,
        status: { type: "requires-action", reason: "composer-send" },
      };
    },
    async send(attachment) {
      const body = new FormData();
      body.append("file", attachment.file);
      const auth = await bearer();
      const res = await fetch(`${API_URL}/api/files`, {
        method: "POST",
        headers: {
          ...userHeader(),
          ...(auth ? { Authorization: `Bearer ${auth}` } : {}),
        },
        body,
      });
      if (!res.ok) {
        let detail = "";
        try {
          const parsed = await res.json();
          detail = typeof parsed.detail === "string" ? parsed.detail : "";
        } catch {
          /* non-JSON body: fall through to the status line */
        }
        // REPORTED as well as thrown. Throwing is what keeps the composer's
        // draft (aui restores text and attachments when send() rejects), but
        // the rejection then travels into useComposerSend's fire-and-forget
        // call and dies unhandled — so the backend's usable sentence ("the
        // file is larger than 8 MB") reached the console and nowhere a person
        // looks.
        const said = detail || `The file was not attached (${res.status}).`;
        reportStatus(said);
        throw new Error(said);
      }
      const stored = await res.json();
      // this attachment is leaving the composer, so it frees its slot. Only on
      // success: a refused upload stays in the restored draft.
      // ponytail: a partial failure (one of three refused) under-counts and
      // lets a later message stage a sixth, which the backend then refuses
      // with a sentence the person can now read. Track the composer's own list
      // if that ever matters.
      count = Math.max(0, count - 1);
      return {
        ...attachment,
        id: String(stored.id),
        name: stored.title,
        status: { type: "complete" },
        content: [],
      };
    },
    async remove() {
      // nothing is stored until send(), so there is nothing to take back —
      // only the staged count, so a removed file frees its slot
      count = Math.max(0, count - 1);
    },
  };
}

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
        // the ids the attachment adapter got back from POST /api/files. The
        // backend resolves them owner-scoped and builds the model's content
        // blocks, so no file content crosses this boundary twice.
        body: JSON.stringify({
          thread_id: threadId,
          message: text,
          attachments: (last.attachments ?? [])
            .map((a) => Number(a.id))
            .filter((id) => Number.isInteger(id) && id > 0),
        }),
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
        if (res.status === 404) {
          window.dispatchEvent(
            new CustomEvent("skein-chat-missing", { detail: { threadId } }),
          );
          throw new Error(
            "Message not sent. This chat is not available. Select New chat. Then send the message again.",
          );
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
        else if (event.type === "error") acc += `\n\n> ${event.message}\n`;
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
            // `acc` and not just "a frame arrived": a thinking model streams
            // empty text deltas for seconds before its first word (measured
            // at 4.4s on glm-5.2, 70 empty frames), and yielding those makes
            // a message with an empty text part. That renders as an empty
            // bubble and — because the message HAS a part — suppresses the
            // Empty slot components/thread.tsx puts the working indicator in.
            if (handle(chunk) !== null && acc) {
              yield { content: [{ type: "text", text: acc }] };
            }
          }
        }
        buffer += decoder.decode(); // flush a truncated tail on abrupt close
        if (buffer && handle(buffer) !== null && acc) {
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
      ? `**Queued for review** — ${e.entity}${ref}${actor} needs a human verdict`
      : e.kind === "wrote"
        ? `**Wrote ${e.entity}${ref}${actor}**`
        : e.kind === "refused"
          ? `**Refused** — Skein prevented ${e.actor || "this agent"} from writing ${e.entity}`
          : e.kind === "nothing"
            ? `**Filed nothing**`
            : e.kind === "unnotified"
              ? `**Not notified** — ${e.entity}`
              : `**Not written** — ${e.entity}${actor}`;
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
  const attachments = useMemo(() => makeAttachmentAdapter(), []);
  const runtime = useLocalRuntime(adapter, { adapters: { attachments } });
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ThreadHydrator threadId={threadId}>{children}</ThreadHydrator>
    </AssistantRuntimeProvider>
  );
}
