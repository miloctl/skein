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

const LAST_KEY = "skein-last-chat";

function initialThread(): string {
  // reopen where you left off — a daily driver must not forget your chat
  // every time you visit another page (unsaved blanks leave no residue)
  try {
    return sessionStorage.getItem(LAST_KEY) || newId();
  } catch {
    return newId();
  }
}

export default function ChatPage() {
  const [threadId, setThreadId] = useState<string>(initialThread);
  const [threads, setThreads] = useState<ChatThread[]>([]);
  const [loadError, setLoadError] = useState(false);

  useEffect(() => {
    try {
      sessionStorage.setItem(LAST_KEY, threadId);
    } catch {}
  }, [threadId]);

  const load = useCallback(() => {
    api<ChatThread[]>("/api/chats")
      .then((rows) => {
        setThreads(rows);
        setLoadError(false);
      })
      .catch(() => setLoadError(true));
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
    document
      .getElementById(`chat-${id}`)
      ?.scrollIntoView({ block: "nearest" });
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
    try {
      await api(`/api/chats/${t.id}`, { method: "DELETE" });
      if (t.id === threadId) startNew();
      load();
    } catch (e) {
      alert(String(e));
    }
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
        {loadError && (
          <p className="px-1 text-xs text-danger">
            Couldn’t load your chats — is the backend running?
          </p>
        )}
        {!threads.some((t) => t.id === threadId) && (
          <div className="mb-2 truncate rounded-lg bg-thread/10 px-2 py-1.5 text-sm font-medium text-ink">
            New chat
            <span className="ml-1.5 text-xs font-normal text-ink-3">
              — saved after your first message
            </span>
          </div>
        )}
        {threads.length === 0 && !loadError && (
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
                  <li key={t.id} id={`chat-${t.id}`} className="group relative">
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
                    <span
                      className={
                        "absolute right-1 top-1/2 -translate-y-1/2 gap-0.5 group-focus-within:flex group-hover:flex " +
                        (t.id === threadId ? "flex" : "hidden")
                      }
                    >
                      <button
                        onClick={() => rename(t)}
                        title="Rename"
                        aria-label={`Rename ${t.title}`}
                        className="rounded p-1 text-xs hover:bg-line"
                      >
                        ✏️
                      </button>
                      <button
                        onClick={() => move(t)}
                        title="Move to folder"
                        aria-label={`Move ${t.title} to a folder`}
                        className="rounded p-1 text-xs hover:bg-line"
                      >
                        📁
                      </button>
                      <button
                        onClick={() => remove(t)}
                        title="Delete chat"
                        aria-label={`Delete ${t.title}`}
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
