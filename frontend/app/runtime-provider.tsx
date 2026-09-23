"use client";

import { createContext, useContext, useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import {
  AssistantRuntimeProvider,
  ExportedMessageRepository,
  useLocalRuntime,
  useAui,
  type AttachmentAdapter,
  type ChatModelAdapter,
  type ThreadMessageLike,
} from "@assistant-ui/react";

import {
  actionError,
  api,
  authenticatedFetch,
} from "@/lib/api";
import { reportStatus } from "@/lib/status";
import { chatThreads } from "@/lib/chat-threads";
import { outgoing } from "@/lib/persona";
import { checkSessionRevision, sessionRevision, subscribeSession } from "@/lib/auth";

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
      const owner = sessionRevision();
      const body = new FormData();
      body.append("file", attachment.file);
      const res = await authenticatedFetch("/api/files", { method: "POST", body });
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
        // A late upload refusal must not become the next identity's status.
        checkSessionRevision(owner);
        const said = detail || `The file was not attached (${res.status}).`;
        reportStatus(said);
        throw new Error(said);
      }
      const stored = await res.json();
      checkSessionRevision(owner);
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

const STREAM_INTERRUPTED =
  "The connection closed before the reply was complete. Reload the page to see what Skein saved.";

/** Streams from the FastAPI backend, which emits SSE lines of
 *  {"type": "masthead" | "text" | "tool" | "receipt" | "error" | "done", ...}.
 *
 *  The thread id is owned by the chat page (sidebar); the backend logs a
 *  provider-agnostic transcript per thread, which ThreadHydrator loads on
 *  mount so switching chats restores history. */
function makeAdapter(threadId: string): ChatModelAdapter {
  return {
    async *run({ messages, abortSignal }) {
      const owner = sessionRevision();
      const last = messages[messages.length - 1];
      // sticky persona: freeform text is invisibly prefixed with /as <slug>
      const text = outgoing(
        last.content
          .filter((p) => p.type === "text")
          .map((p) => p.text)
          .join("\n"),
      );

      // authenticatedFetch owns the credential ladder and 401 session update.
      // Chat uses it directly because api() consumes the streaming response.
      const res = await authenticatedFetch("/api/chat", {
        method: "POST",
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
        // A late or aborted 404 body must not close the new identity's thread.
        checkSessionRevision(owner);
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
      let shown = false;
      // every backend stream path ends with a done frame (routes/chat.py). A
      // body that closes without one was cut, by a restart or a dropped
      // connection, and its partial text otherwise reads as a whole reply.
      let finished = false;

      const handle = (chunk: string): string | null => {
        if (!chunk.startsWith("data: ")) return null;
        let event;
        try {
          event = JSON.parse(chunk.slice(6));
        } catch {
          return null; // tolerate malformed lines (e.g. proxy keep-alives)
        }
        if (event.type === "text") acc += event.text;
        else if (event.type === "masthead") {
          // the nameplate is kept until the first word: yielded alone it is a
          // text part, and a message WITH a part suppresses the Empty slot
          // components/thread.tsx puts the working indicator in — so a
          // persona turn sits behind a bare nameplate for the whole model wait
          acc += event.text;
          return null;
        } else if (event.type === "tool") acc += `\n\n*🔧 ${event.name}…*\n\n`;
        else if (event.type === "receipt") acc += receiptLine(event);
        else if (event.type === "error") acc += `\n\n> ${event.message}\n`;
        else if (event.type === "done") {
          finished = true;
          return null;
        } else return null;
        return acc;
      };

      try {
        while (true) {
          const { done, value } = await reader.read();
          checkSessionRevision(owner);
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const chunks = buffer.split("\n\n");
          buffer = chunks.pop() ?? "";
          for (const chunk of chunks) {
            checkSessionRevision(owner);
            // `acc` and not just "a frame arrived": a thinking model streams
            // empty text deltas for seconds before its first word (measured
            // at 4.4s on glm-5.2, 70 empty frames), and yielding those makes
            // a message with an empty text part. That renders as an empty
            // bubble and — because the message HAS a part — suppresses the
            // Empty slot components/thread.tsx puts the working indicator in.
            if (handle(chunk) !== null && acc) {
              shown = true;
              yield { content: [{ type: "text", text: acc }] };
            }
          }
        }
        checkSessionRevision(owner);
        buffer += decoder.decode(); // flush a truncated tail on abrupt close
        if (buffer && handle(buffer) !== null && acc) {
          yield { content: [{ type: "text", text: acc }] };
        } else if (acc && !shown) {
          // a masthead with no word after it (an empty reply, a stop before
          // the first token): the transcript stores the nameplate, so the
          // bubble shows it too rather than "The turn ended without a reply."
          yield { content: [{ type: "text", text: acc }] };
        }
        if (!finished && !abortSignal?.aborted) {
          acc += `\n\n> ${STREAM_INTERRUPTED}\n`;
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

type StoredMessage = { id: number; role: "user" | "assistant"; content: string; created_at: string };
type MessagePage = { messages: StoredMessage[]; next_before: number | null };
const restoredMessage = (m: StoredMessage): ThreadMessageLike => ({
  id: `saved-${m.id}`,
  role: m.role,
  content: [{ type: "text", text: m.content }],
  createdAt: new Date(m.created_at),
});
const HistoryContext = createContext<{
  saved: boolean;
  before: number | null;
  loading: boolean;
  error: string;
  loadOlder: (beforePrepend: () => void) => Promise<void>;
} | null>(null);
export const useTranscriptHistory = () => useContext(HistoryContext);

/** The keyed runtime owns both its messages and its cursor. No history request
 *  can reset a different thread or identity, including a late cached list. */
function ThreadHydrator({ threadId, children }: { threadId: string; children: ReactNode }) {
  const thread = useAui().thread;
  const [ready, setReady] = useState(false);
  const [saved, setSaved] = useState(false);
  const [before, setBefore] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const request = useRef<AbortController | null>(null);
  const [owner] = useState(sessionRevision);
  useEffect(() => {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => {
      controller.abort();
      if (owner === sessionRevision())
        setError("Saved messages did not load in time. Select Retry saved messages.");
    }, 15_000);
    chatThreads(retry > 0).then(async (rows) => {
      checkSessionRevision(owner);
      if (controller.signal.aborted) return;
      const exists = rows.some((t) => t.id === threadId);
      const page = exists
        ? await api<MessagePage>(`/api/chats/${threadId}/messages/page`, { cache: "no-store", signal: controller.signal })
        : { messages: [], next_before: null };
      // api() checks its own response binding too. The list can outlive this
      // component or its identity before a request even starts.
      checkSessionRevision(owner);
      if (controller.signal.aborted) return;
      if (page.messages.length > 0 && thread.getState().messages.length === 0)
        thread.reset(page.messages.map(restoredMessage));
      setSaved(exists);
      setBefore(page.next_before);
      setError("");
      setReady(true);
    }).catch((e) => {
      if (controller.signal.aborted || owner !== sessionRevision()) return;
      const message = `This chat's saved messages did not load. ${actionError(e)}`;
      setError(message);
      reportStatus(message);
    }).finally(() => window.clearTimeout(timeout));
    return () => {
      window.clearTimeout(timeout);
      controller.abort();
      request.current?.abort();
      request.current = null;
    };
  }, [thread, threadId, owner, retry]);

  async function loadOlder(beforePrepend: () => void) {
    if (before === null || request.current || owner !== sessionRevision()) return;
    const controller = new AbortController();
    request.current = controller;
    const timeout = window.setTimeout(() => controller.abort(), 15_000);
    setLoading(true);
    setError("");
    try {
      const page = await api<MessagePage>(`/api/chats/${threadId}/messages/page?before=${before}`, {
        cache: "no-store", signal: controller.signal,
      });
      checkSessionRevision(owner);
      if (controller.signal.aborted) return;
      // Read AFTER the fetch: a new turn can stream while this page loads.
      // Import retains the live head, IDs, status, objects and draft. Appending
      // saved user messages instead would start fresh model turns.
      const current = thread.export();
      const ids = new Set(current.messages.map(({ message }) => message.id));
      const older = ExportedMessageRepository.fromArray(page.messages.map(restoredMessage).filter((m) => !ids.has(m.id!)));
      if (older.messages.length) {
        beforePrepend();
        const parent = older.messages.at(-1)!.message.id;
        thread.import({
          ...current,
          messages: [...older.messages, ...current.messages.map((item) => item.parentId === null ? { ...item, parentId: parent } : item)],
        });
      }
      setBefore(page.next_before);
    } catch (e) {
      if (owner === sessionRevision() && request.current === controller)
        setError(controller.signal.aborted
          ? "Older messages did not load in time. Select Load older messages to try again."
          : `Older messages did not load. ${actionError(e)} Select Load older messages to try again.`);
    } finally {
      window.clearTimeout(timeout);
      if (request.current === controller) {
        request.current = null;
        setLoading(false);
      }
    }
  }
  if (!ready) return (
    <div className="p-8 text-sm text-ink-3">
      {error ? <><p role="alert">{error}</p><button type="button" className="mt-3 underline" onClick={() => { setError(""); setRetry((n) => n + 1); }}>Retry saved messages</button></>
        : <p>Unrolling the transcript…</p>}
    </div>
  );
  return <HistoryContext.Provider value={{ saved, before, loading, error, loadOlder }}>{children}</HistoryContext.Provider>;
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

export function RuntimeProvider({ threadId, children }: { threadId: string; children: ReactNode }) {
  const identity = useSyncExternalStore(subscribeSession, sessionRevision, () => "");
  return <BoundRuntime key={JSON.stringify([threadId, identity])} threadId={threadId}>{children}</BoundRuntime>;
}

function BoundRuntime({ threadId, children }: { threadId: string; children: ReactNode }) {
  const adapter = useMemo(() => makeAdapter(threadId), [threadId]);
  const attachments = useMemo(() => makeAttachmentAdapter(), []);
  const runtime = useLocalRuntime(adapter, { adapters: { attachments } });
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ThreadHydrator threadId={threadId}>{children}</ThreadHydrator>
    </AssistantRuntimeProvider>
  );
}
