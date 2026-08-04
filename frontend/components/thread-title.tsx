"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { actionError, api } from "@/lib/api";
import { chatThreads } from "@/lib/chat-threads";

/** The conversation's name, as the page's h1. With the sidebar closed this
 *  was the only thing telling you which of your chats you were in — and the
 *  only rename path was three levels into a per-row menu. */
export function ThreadTitle({ threadId }: { threadId: string }) {
  const [title, setTitle] = useState("");
  const [editing, setEditing] = useState(false);
  const btnRef = useRef<HTMLButtonElement>(null);

  const load = useCallback(() => {
    // shared single-flight list (lib/chat-threads.ts) — the sidebar reads
    // the same fetch, so each activity event costs one request, not two
    chatThreads()
      .then((rows) => setTitle(rows.find((t) => t.id === threadId)?.title ?? ""))
      .catch(() => {});
  }, [threadId]);

  useEffect(() => {
    load();
    window.addEventListener("skein-chat-activity", load);
    return () => window.removeEventListener("skein-chat-activity", load);
  }, [load]);

  useEffect(() => {
    document.title = title ? `${title} — Skein` : "Chat — Skein";
  }, [title]);

  // deferred: the button carrying btnRef is unmounted while editing, so an
  // immediate focus() hits a null ref and focus falls to <body> — the ref
  // attaches when the non-editing branch re-renders, before the timeout runs
  const refocus = () => setTimeout(() => btnRef.current?.focus(), 0);

  const save = async (next: string) => {
    const name = next.trim();
    setEditing(false);
    refocus();
    if (!name || name === title) return;
    setTitle(name); // optimistic: the sidebar refreshes off the same event
    try {
      await api(`/api/chats/${threadId}`, {
        method: "PATCH",
        body: JSON.stringify({ title: name }),
      });
      window.dispatchEvent(new Event("skein-chat-activity"));
    } catch (e) {
      load();
      alert(actionError(e));
    }
  };

  if (editing)
    return (
      <input
        autoFocus
        defaultValue={title}
        aria-label="Conversation name"
        maxLength={120}
        onKeyDown={(e) => {
          if (e.key === "Enter") save((e.target as HTMLInputElement).value);
          if (e.key === "Escape") {
            setEditing(false);
            refocus();
          }
        }}
        onBlur={(e) => save(e.target.value)}
        className="min-w-0 flex-1 rounded-lg border border-line-strong bg-transparent px-2 py-1 text-sm outline-none focus:border-thread-solid"
      />
    );

  return (
    <h1 className="min-w-0 flex-1">
      <button
        ref={btnRef}
        onClick={() => title && setEditing(true)}
        title={title ? "Rename this conversation" : undefined}
        className="block max-w-full truncate rounded px-1 py-0.5 text-left font-display text-[15px]/[1.2] font-semibold tracking-[-0.01em] text-ink hover:bg-raised disabled:hover:bg-transparent"
        disabled={!title}
      >
        {title || (
          <span className="font-normal text-ink-3">
            New chat
            <span className="ml-1.5 hidden text-xs sm:inline">
              — saved after your first message
            </span>
          </span>
        )}
      </button>
    </h1>
  );
}
