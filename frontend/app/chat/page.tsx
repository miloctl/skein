"use client";

import { useCallback, useEffect, useState } from "react";

import { RuntimeProvider } from "../runtime-provider";
import { Thread } from "@/components/thread";
import { api } from "@/lib/api";
import { setActivePersona } from "@/lib/persona";

type ChatThread = {
  id: string;
  title: string;
  folder: string;
  updated_at: string;
};

function newId() {
  return crypto.randomUUID();
}

export default function ChatPage() {
  const [threadId, setThreadId] = useState<string>(newId);
  const [threads, setThreads] = useState<ChatThread[]>([]);

  const load = useCallback(() => {
    api<ChatThread[]>("/api/chats").then(setThreads).catch(() => {});
  }, []);

  useEffect(() => {
    load();
    window.addEventListener("skein-chat-activity", load);
    return () => window.removeEventListener("skein-chat-activity", load);
  }, [load]);

  const open = (id: string) => {
    if (id === threadId) return;
    setActivePersona(null); // persona mode is per-conversation
    setThreadId(id);
  };

  const startNew = () => {
    setActivePersona(null);
    setThreadId(newId());
  };

  const rename = async (t: ChatThread) => {
    const title = prompt("Chat name:", t.title);
    if (!title?.trim()) return;
    await api(`/api/chats/${t.id}`, {
      method: "PATCH",
      body: JSON.stringify({ title: title.trim() }),
    }).catch((e) => alert(String(e)));
    load();
  };

  const move = async (t: ChatThread) => {
    const existing = [...new Set(threads.map((x) => x.folder).filter(Boolean))];
    const folder = prompt(
      `Folder (empty to unfile)${existing.length ? ` — existing: ${existing.join(", ")}` : ""}:`,
      t.folder,
    );
    if (folder === null) return;
    await api(`/api/chats/${t.id}`, {
      method: "PATCH",
      body: JSON.stringify({ folder: folder.trim() }),
    }).catch((e) => alert(String(e)));
    load();
  };

  const remove = async (t: ChatThread) => {
    if (!confirm(`Delete “${t.title}”? The transcript is gone for good.`)) return;
    await api(`/api/chats/${t.id}`, { method: "DELETE" }).catch((e) =>
      alert(String(e)),
    );
    if (t.id === threadId) startNew();
    load();
  };

  // unfiled chats first, then folders alphabetically — recency within each
  const folders = [...new Set(threads.map((t) => t.folder))].sort((a, b) =>
    a === "" ? -1 : b === "" ? 1 : a.localeCompare(b),
  );

  return (
    <div className="mx-auto flex h-[calc(100vh-3.5rem)] w-full max-w-6xl">
      <aside className="hidden w-64 shrink-0 flex-col overflow-y-auto border-r border-line p-3 md:flex">
        <button
          onClick={startNew}
          className="mb-3 rounded-lg bg-thread-solid px-3 py-1.5 text-sm font-medium text-white hover:opacity-90"
        >
          + New chat
        </button>
        {threads.length === 0 && (
          <p className="px-1 text-xs text-ink-3">
            Your chats appear here after the first message — rename them or
            file them into folders to keep threads you return to.
          </p>
        )}
        {folders.map((folder) => (
          <div key={folder || "(none)"} className="mb-2">
            {folder && (
              <p className="mb-1 mt-2 px-1 font-mono text-[10px] font-medium uppercase tracking-[0.12em] text-ink-3">
                📁 {folder}
              </p>
            )}
            <ul className="space-y-0.5">
              {threads
                .filter((t) => t.folder === folder)
                .map((t) => (
                  <li key={t.id} className="group relative">
                    <button
                      onClick={() => open(t.id)}
                      className={
                        "w-full truncate rounded-lg px-2 py-1.5 pr-16 text-left text-sm transition-colors " +
                        (t.id === threadId
                          ? "bg-thread/10 font-medium text-ink"
                          : "text-ink-2 hover:bg-raised")
                      }
                      title={t.title}
                    >
                      {t.title}
                    </button>
                    <span className="absolute right-1 top-1/2 hidden -translate-y-1/2 gap-0.5 group-hover:flex">
                      <button
                        onClick={() => rename(t)}
                        title="Rename"
                        className="rounded p-1 text-xs hover:bg-line"
                      >
                        ✏️
                      </button>
                      <button
                        onClick={() => move(t)}
                        title="Move to folder"
                        className="rounded p-1 text-xs hover:bg-line"
                      >
                        📁
                      </button>
                      <button
                        onClick={() => remove(t)}
                        title="Delete chat"
                        className="rounded p-1 text-xs hover:bg-line"
                      >
                        🗑
                      </button>
                    </span>
                  </li>
                ))}
            </ul>
          </div>
        ))}
      </aside>
      <RuntimeProvider key={threadId} threadId={threadId}>
        <main className="mx-auto flex h-full w-full max-w-3xl flex-col">
          <Thread />
        </main>
      </RuntimeProvider>
    </div>
  );
}
