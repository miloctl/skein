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
  const [folders, setFolders] = useState<string[]>([]);
  const [dropTarget, setDropTarget] = useState<string | null>(null);
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
    api<string[]>("/api/chats/folders").then(setFolders).catch(() => {});
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

  const newFolder = async () => {
    const name = prompt("Folder name:");
    if (!name?.trim()) return;
    await api("/api/chats/folders", {
      method: "POST",
      body: JSON.stringify({ name: name.trim() }),
    }).catch((e) => alert(String(e)));
    load();
  };

  const deleteFolder = async (name: string) => {
    if (!confirm(`Delete folder “${name}”? Its chats stay, unfiled.`)) return;
    await api(`/api/chats/folders/${encodeURIComponent(name)}`, {
      method: "DELETE",
    }).catch((e) => alert(String(e)));
    load();
  };

  const dropInto = async (e: React.DragEvent, folder: string) => {
    e.preventDefault();
    e.stopPropagation();
    setDropTarget(null);
    const id = e.dataTransfer.getData("text/plain");
    if (!id) return;
    await api(`/api/chats/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ folder }),
    }).catch((err) => alert(String(err)));
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

  // unfiled chats first, then every folder (including empty ones)
  const groups = ["", ...folders];

  return (
    <div className="mx-auto flex h-[calc(100vh-3.5rem)] w-full max-w-6xl">
      <aside
        onDragOver={(e) => {
          e.preventDefault();
          setDropTarget("");
        }}
        onDrop={(e) => dropInto(e, "")}
        onDragLeave={() => setDropTarget(null)}
        className={
          "hidden w-64 shrink-0 flex-col overflow-y-auto border-r p-3 md:flex " +
          (dropTarget === "" ? "border-thread-solid bg-thread/5" : "border-line")
        }
      >
        <div className="mb-3 flex gap-1.5">
          <button
            onClick={startNew}
            className="flex-1 rounded-lg bg-thread-solid px-3 py-1.5 text-sm font-medium text-white hover:opacity-90"
          >
            + New chat
          </button>
          <button
            onClick={newFolder}
            title="New folder"
            aria-label="New folder"
            className="rounded-lg border border-line-strong px-2.5 py-1.5 text-sm text-ink-2 hover:bg-raised"
          >
            📁+
          </button>
        </div>
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
        {groups.map((folder) => (
          <div
            key={folder || "(none)"}
            onDragOver={
              folder
                ? (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    setDropTarget(folder);
                  }
                : undefined
            }
            onDrop={folder ? (e) => dropInto(e, folder) : undefined}
            className={
              "mb-2 rounded-lg " +
              (folder && dropTarget === folder
                ? "bg-thread/10 ring-1 ring-thread-solid"
                : "")
            }
          >
            {folder && (
              <p className="group/folder mb-1 mt-2 flex items-center justify-between px-1 font-mono text-[10px] font-medium uppercase tracking-[0.12em] text-ink-3">
                <span>📁 {folder}</span>
                <button
                  onClick={() => deleteFolder(folder)}
                  title="Delete folder (chats stay, unfiled)"
                  aria-label={`Delete folder ${folder}`}
                  className="hidden rounded px-1 hover:bg-line group-hover/folder:block"
                >
                  ×
                </button>
              </p>
            )}
            {folder &&
              threads.filter((t) => t.folder === folder).length === 0 && (
                <p className="px-2 pb-1 text-[11px] italic text-ink-3">
                  drag chats here
                </p>
              )}
            <ul className="space-y-0.5">
              {threads
                .filter((t) => t.folder === folder)
                .map((t) => (
                  <li key={t.id} id={`chat-${t.id}`} className="group relative">
                    <button
                      onClick={() => open(t.id)}
                      draggable
                      onDragStart={(e) =>
                        e.dataTransfer.setData("text/plain", t.id)
                      }
                      className={
                        "w-full cursor-grab truncate rounded-lg px-2 py-1.5 pr-16 text-left text-sm transition-colors active:cursor-grabbing " +
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
