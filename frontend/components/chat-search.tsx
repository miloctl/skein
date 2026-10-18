"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Snippet } from "@/components/snippet";
import { actionError, api, backendUnreachable, isUnreachable } from "@/lib/api";
import { sessionRevision } from "@/lib/auth";
import { subscribeIdentity } from "@/lib/shared-chats";
import { timeAgo } from "@/lib/time";

type ChatHit = {
  thread_id: string;
  kind: "solo" | "shared";
  title: string;
  message_id: number | null;
  matched_at: string;
  snippet: string;
};
type Found = { results: ChatHit[]; truncated: boolean };

const matchLine = (n: number) =>
  n === 0 ? "No chat matches these words." : `${n} ${n === 1 ? "chat matches" : "chats match"}.`;

/** Search inside the reader's solo chats and the shared chats they belong to
 *  (GET /api/chats/search). Enter runs it and Escape clears it. While results
 *  show, chat-sidebar.tsx hides the chat list, which onActiveChange reports. */
export function ChatSearch({
  onOpen,
  onActiveChange,
}: {
  onOpen: (id: string, kind: "solo" | "shared", messageId?: number) => void;
  onActiveChange: (active: boolean) => void;
}) {
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<Found | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  // a slow search must not replace the answer to a newer one
  const latest = useRef(0);

  // The sidebar's flag changes in the same handler as the results, so the
  // list and the results never render together for a frame.
  const show = useCallback(
    (next: Found | null, problem: string) => {
      setFound(next);
      setError(problem);
      onActiveChange(next !== null || problem !== "");
    },
    [onActiveChange],
  );
  const clear = useCallback(() => {
    latest.current += 1;
    setQuery("");
    setLoading(false);
    show(null, "");
  }, [show]);

  // unmounted (select mode) with results showing, the list must come back
  useEffect(() => () => onActiveChange(false), [onActiveChange]);
  // A result can name a private shared room and quote it. It belongs to the
  // identity that asked, so a switch drops it and any search still running.
  useEffect(() => subscribeIdentity(clear), [clear]);

  async function run() {
    const q = query.trim();
    if (!q) return clear();
    const request = ++latest.current;
    const owner = sessionRevision();
    setLoading(true);
    // A retry that fails the same way reuses the same alert node with the
    // same text, and a screen reader announces nothing. Cleared, it remounts.
    setError("");
    try {
      const result = await api<Found>(`/api/chats/search?q=${encodeURIComponent(q)}`, {
        cache: "no-store",
      });
      if (request !== latest.current || owner !== sessionRevision()) return;
      show(result, "");
    } catch (e) {
      if (request !== latest.current || owner !== sessionRevision()) return;
      // a refusal the server answered is already a sentence for this reader
      // ("The search stopped after 3 seconds."), and a prefix contradicted it
      show(null, isUnreachable(e) ? `The search did not run. ${backendUnreachable(e)}` : actionError(e));
    } finally {
      if (request === latest.current) setLoading(false);
    }
  }

  return (
    <div className="mb-3">
      <form
        role="search"
        aria-label="Chats"
        onSubmit={(e) => {
          e.preventDefault();
          void run();
        }}
      >
        <label htmlFor="chat-search" className="sr-only">
          Search your chats
        </label>
        <input
          id="chat-search"
          type="search"
          enterKeyHint="search"
          maxLength={200}
          value={query}
          placeholder="Search your chats"
          onChange={(e) => {
            setQuery(e.target.value);
            if (!e.target.value) clear();
          }}
          onKeyDown={(e) => {
            // Escape with nothing to clear still reaches the drawer, which closes
            if (e.key === "Escape" && (query || found || error)) {
              e.preventDefault();
              e.stopPropagation();
              clear();
            }
          }}
          className="w-full rounded-lg border border-line-strong bg-transparent px-2.5 py-1.5 text-sm outline-none placeholder:text-ink-3 focus:border-thread-solid"
        />
      </form>
      {/* always mounted: a live region added together with its text is
          often not announced. Empty, it has no line box and no height. */}
      <p role="status" className="px-1 text-xs leading-6 text-ink-3">
        {loading
          ? "Searching…"
          : found?.truncated
            ? "More than 30 chats match. The list shows the 30 chats with the newest matches. To find fewer chats, add a word."
            : found
              ? matchLine(found.results.length)
              : ""}
      </p>
      {error && (
        <p role="alert" className="px-1 pt-1 text-xs text-danger">
          {error}
        </p>
      )}
      {found && found.results.length > 0 && (
        <ul className="mt-1 space-y-0.5">
          {found.results.map((hit) => (
            <li key={hit.thread_id}>
              <button
                type="button"
                onClick={() => {
                  // drops the cached chat list (lib/chat-threads.ts): a chat
                  // made in another tab is in the results but not in that
                  // list, so without this runtime-provider.tsx opens it empty
                  window.dispatchEvent(new Event("skein-chat-activity"));
                  onOpen(hit.thread_id, hit.kind, hit.message_id ?? undefined);
                }}
                className="block w-full rounded-lg px-2 py-1.5 text-left hover:bg-raised"
              >
                <span className="flex items-baseline gap-1.5">
                  <span className="min-w-0 flex-1 truncate text-sm font-medium text-ink">
                    {hit.title}
                  </span>
                  {hit.kind === "shared" && (
                    <span className="shrink-0 text-[10px] font-medium uppercase text-ink-3">
                      Shared
                    </span>
                  )}
                  <span className="shrink-0 text-[11px] text-ink-3">{timeAgo(hit.matched_at)}</span>
                </span>
                {hit.snippet && (
                  <span className="mt-0.5 line-clamp-2 break-words text-xs text-ink-2">
                    <Snippet text={hit.snippet} />
                  </span>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
