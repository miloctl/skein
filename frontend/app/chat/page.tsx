"use client";

import { useCallback, useEffect, useState, useSyncExternalStore } from "react";

import { RuntimeProvider } from "../runtime-provider";
import { Thread } from "@/components/thread";
import { api, getUser } from "@/lib/api";
import { setActivePersona } from "@/lib/persona";

type ChatThread = {
  id: string;
  title: string;
  folder: string;
  updated_at: string;
};

type StoredMessage = { role: "user" | "assistant"; content: string };

function newId() {
  return crypto.randomUUID();
}

const LAST_KEY = "skein-last-chat";
const COLLAPSE_KEY = "skein-chat-sidebar-collapsed";

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
  const [creatingFolder, setCreatingFolder] = useState(false);
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const [movePicker, setMovePicker] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [selectMode, setSelectMode] = useState(false);
  const [selChats, setSelChats] = useState<Set<string>>(new Set());
  const [selFolders, setSelFolders] = useState<Set<string>>(new Set());
  const [sidebarMenu, setSidebarMenu] = useState(false);
  const [loadError, setLoadError] = useState(false);

  // hydration-safe persisted collapse (same pattern as Settings prefs)
  const collapsed = useSyncExternalStore(
    (cb) => {
      window.addEventListener("storage", cb);
      return () => window.removeEventListener("storage", cb);
    },
    () => {
      try {
        return localStorage.getItem(COLLAPSE_KEY) === "1";
      } catch {
        return false;
      }
    },
    () => false,
  );

  const toggleCollapsed = () => {
    try {
      localStorage.setItem(COLLAPSE_KEY, collapsed ? "" : "1");
    } catch {}
    window.dispatchEvent(new Event("storage"));
  };

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

  const closeMenus = () => {
    setMenuFor(null);
    setMovePicker(null);
    setSidebarMenu(false);
  };

  const open = (id: string) => {
    if (id === threadId) return;
    setActivePersona(null); // persona mode is per-conversation
    closeMenus();
    setThreadId(id);
    document.getElementById(`chat-${id}`)?.scrollIntoView({ block: "nearest" });
  };

  const startNew = () => {
    setActivePersona(null);
    closeMenus();
    setThreadId(newId());
  };

  const createFolder = async (name: string) => {
    if (!name.trim()) return;
    setCreatingFolder(false);
    await api("/api/chats/folders", {
      method: "POST",
      body: JSON.stringify({ name: name.trim() }),
    }).catch((e) => alert(String(e)));
    load();
  };

  const setFolder = async (id: string, folder: string) => {
    closeMenus();
    await api(`/api/chats/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ folder }),
    }).catch((e) => alert(String(e)));
    load();
  };

  const rename = async (t: ChatThread) => {
    closeMenus();
    const title = prompt("Chat name:", t.title);
    if (!title?.trim()) return;
    await api(`/api/chats/${t.id}`, {
      method: "PATCH",
      body: JSON.stringify({ title: title.trim() }),
    }).catch((e) => alert(String(e)));
    load();
  };

  const copyTranscript = async (t: ChatThread) => {
    try {
      const msgs = await api<StoredMessage[]>(`/api/chats/${t.id}/messages`);
      const me = getUser();
      const md =
        `# ${t.title}\n\n` +
        msgs
          .map((m) => `**${m.role === "user" ? me : "Skein"}:**\n\n${m.content}`)
          .join("\n\n---\n\n");
      await navigator.clipboard.writeText(md);
      setCopied(t.id);
      setTimeout(() => {
        setCopied(null);
        setMenuFor(null);
      }, 900);
    } catch (e) {
      alert(String(e));
    }
  };

  const remove = async (t: ChatThread) => {
    closeMenus();
    if (!confirm(`Delete “${t.title}”? The transcript is gone for good.`)) return;
    try {
      await api(`/api/chats/${t.id}`, { method: "DELETE" });
      if (t.id === threadId) startNew();
      load();
    } catch (e) {
      alert(String(e));
    }
  };

  const enterSelect = (seedChat?: string) => {
    closeMenus();
    setSelectMode(true);
    setSelChats(new Set(seedChat ? [seedChat] : []));
    setSelFolders(new Set());
  };

  const exitSelect = () => {
    setSelectMode(false);
    setSelChats(new Set());
    setSelFolders(new Set());
  };

  const toggleSet = (set: Set<string>, key: string) => {
    const next = new Set(set);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    return next;
  };

  const deleteSelected = async () => {
    const nChats = selChats.size;
    const nFolders = selFolders.size;
    if (nChats + nFolders === 0) return;
    const parts = [
      nChats ? `${nChats} chat(s) — transcripts gone for good` : "",
      nFolders ? `${nFolders} folder(s) — their chats stay, unfiled` : "",
    ].filter(Boolean);
    if (!confirm(`Delete ${parts.join(" and ")}?`)) return;
    try {
      for (const id of selChats) {
        await api(`/api/chats/${id}`, { method: "DELETE" });
      }
      for (const name of selFolders) {
        await api(`/api/chats/folders/${encodeURIComponent(name)}`, {
          method: "DELETE",
        });
      }
      if (selChats.has(threadId)) startNew();
      exitSelect();
      load();
    } catch (e) {
      alert(String(e));
      load();
    }
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

  // unfiled chats first, then every folder (including empty ones)
  const groups = ["", ...folders];
  const nSelected = selChats.size + selFolders.size;

  return (
    <div className="mx-auto flex h-[calc(100vh-3.5rem)] w-full max-w-6xl">
      {collapsed && (
        <button
          onClick={toggleCollapsed}
          title="Show chats"
          aria-label="Show chat sidebar"
          className="hidden h-full w-6 shrink-0 border-r border-line text-xs text-ink-3 hover:bg-raised hover:text-ink md:block"
        >
          »
        </button>
      )}
      <aside
        onDragOver={(e) => {
          e.preventDefault();
          setDropTarget("");
        }}
        onDrop={(e) => dropInto(e, "")}
        onDragLeave={() => setDropTarget(null)}
        className={
          "w-64 shrink-0 flex-col overflow-y-auto border-r p-3 " +
          (collapsed ? "hidden " : "hidden md:flex ") +
          (dropTarget === "" ? "border-thread-solid bg-thread/5" : "border-line")
        }
      >
        {selectMode ? (
          <div className="mb-3 flex items-center gap-1.5 rounded-lg border border-line bg-raised px-2 py-1.5 text-xs">
            <span className="flex-1 font-medium">{nSelected} selected</span>
            <button
              onClick={deleteSelected}
              disabled={nSelected === 0}
              className="rounded bg-danger/15 px-2 py-1 font-medium text-danger hover:bg-danger/20 disabled:opacity-40"
            >
              Delete
            </button>
            <button
              onClick={exitSelect}
              className="rounded px-2 py-1 text-ink-2 hover:bg-line"
            >
              Cancel
            </button>
          </div>
        ) : (
          <div className="mb-3 flex gap-1.5">
            <button
              onClick={startNew}
              className="flex-1 rounded-lg bg-thread-solid px-3 py-1.5 text-sm font-medium text-white hover:opacity-90"
            >
              + New chat
            </button>
            <button
              onClick={() => setSidebarMenu((v) => !v)}
              title="More"
              aria-label="Chat list options"
              aria-expanded={sidebarMenu}
              className="rounded-lg border border-line-strong px-2.5 py-1.5 text-sm text-ink-2 hover:bg-raised"
            >
              ⋯
            </button>
            <button
              onClick={toggleCollapsed}
              title="Collapse sidebar"
              aria-label="Collapse sidebar"
              className="rounded-lg border border-line-strong px-2 py-1.5 text-sm text-ink-2 hover:bg-raised"
            >
              «
            </button>
          </div>
        )}
        {sidebarMenu && !selectMode && (
          <div className="mb-2 rounded-lg border border-line bg-card p-1 shadow-float">
            {(
              [
                [
                  "📁 New folder",
                  () => {
                    setSidebarMenu(false);
                    setCreatingFolder(true);
                  },
                ],
                [
                  "☑️ Select chats or folders",
                  () => {
                    setSidebarMenu(false);
                    enterSelect();
                  },
                ],
              ] as const
            ).map(([label, fn]) => (
              <button
                key={label}
                onClick={fn}
                className="block w-full rounded px-2 py-1.5 text-left text-xs text-ink-2 hover:bg-raised"
              >
                {label}
              </button>
            ))}
          </div>
        )}
        {creatingFolder && !selectMode && (
          <input
            autoFocus
            placeholder="Folder name — ↵ to create, esc to cancel"
            onKeyDown={(e) => {
              if (e.key === "Enter") createFolder(e.currentTarget.value);
              if (e.key === "Escape") setCreatingFolder(false);
            }}
            onBlur={() => setCreatingFolder(false)}
            className="mb-2 rounded-lg border border-thread-solid bg-transparent px-2 py-1.5 text-sm outline-none placeholder:text-ink-3"
          />
        )}
        {loadError && (
          <p className="px-1 text-xs text-danger">
            Couldn’t load your chats — is the backend running?
          </p>
        )}
        {!selectMode && !threads.some((t) => t.id === threadId) && (
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
              <p className="mb-1 mt-2 flex items-center gap-1.5 px-1 font-mono text-[10px] font-medium uppercase tracking-[0.12em] text-ink-3">
                {selectMode && (
                  <input
                    type="checkbox"
                    checked={selFolders.has(folder)}
                    onChange={() => setSelFolders(toggleSet(selFolders, folder))}
                    aria-label={`Select folder ${folder}`}
                    className="h-3 w-3"
                  />
                )}
                <span className="flex-1">📁 {folder}</span>
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
                    <span className="flex items-center gap-1.5">
                      {selectMode && (
                        <input
                          type="checkbox"
                          checked={selChats.has(t.id)}
                          onChange={() => setSelChats(toggleSet(selChats, t.id))}
                          aria-label={`Select ${t.title}`}
                          className="ml-1 h-3.5 w-3.5 shrink-0"
                        />
                      )}
                      <button
                        onClick={() =>
                          selectMode
                            ? setSelChats(toggleSet(selChats, t.id))
                            : open(t.id)
                        }
                        draggable={!selectMode}
                        onDragStart={(e) =>
                          e.dataTransfer.setData("text/plain", t.id)
                        }
                        className={
                          "min-w-0 flex-1 truncate rounded-lg px-2 py-1.5 text-left text-sm transition-colors " +
                          (selectMode ? "" : "cursor-grab active:cursor-grabbing pr-8 ") +
                          (t.id === threadId && !selectMode
                            ? "bg-thread/10 font-medium text-ink"
                            : "text-ink-2 hover:bg-raised")
                        }
                        title={t.title}
                      >
                        {t.title}
                      </button>
                    </span>
                    {!selectMode && (
                      <button
                        onClick={() => {
                          setMovePicker(null);
                          setMenuFor(menuFor === t.id ? null : t.id);
                        }}
                        title="More"
                        aria-label={`More actions for ${t.title}`}
                        aria-expanded={menuFor === t.id}
                        className={
                          "absolute right-1 top-1/2 -translate-y-1/2 rounded px-1.5 py-0.5 text-sm text-ink-3 hover:bg-line group-focus-within:block group-hover:block " +
                          (menuFor === t.id || t.id === threadId
                            ? "block"
                            : "hidden")
                        }
                      >
                        ⋯
                      </button>
                    )}
                    {menuFor === t.id && !selectMode && (
                      <div className="mt-1 rounded-lg border border-line bg-card p-1 shadow-float">
                        {(
                          [
                            ["✏️ Rename", () => rename(t)],
                            [
                              "📁 Move to folder…",
                              () => {
                                setMenuFor(null);
                                setMovePicker(t.id);
                              },
                            ],
                            [
                              copied === t.id
                                ? "✓ Copied"
                                : "📋 Copy as Markdown",
                              () => copyTranscript(t),
                            ],
                            ["☑️ Select…", () => enterSelect(t.id)],
                            ["🗑 Delete", () => remove(t)],
                          ] as const
                        ).map(([label, fn]) => (
                          <button
                            key={label}
                            onClick={fn}
                            className={
                              "block w-full rounded px-2 py-1.5 text-left text-xs hover:bg-raised " +
                              (label === "🗑 Delete"
                                ? "text-danger"
                                : "text-ink-2")
                            }
                          >
                            {label}
                          </button>
                        ))}
                      </div>
                    )}
                    {movePicker === t.id && !selectMode && (
                      <div className="mt-1 rounded-lg border border-line bg-card p-1.5 shadow-float">
                        <p className="mb-1 px-1 font-mono text-[10px] font-medium uppercase tracking-[0.12em] text-ink-3">
                          Move to
                        </p>
                        {t.folder && (
                          <button
                            onClick={() => setFolder(t.id, "")}
                            className="block w-full rounded px-2 py-1 text-left text-xs text-ink-2 hover:bg-raised"
                          >
                            ⊘ Unfiled
                          </button>
                        )}
                        {folders
                          .filter((f) => f !== t.folder)
                          .map((f) => (
                            <button
                              key={f}
                              onClick={() => setFolder(t.id, f)}
                              className="block w-full truncate rounded px-2 py-1 text-left text-xs text-ink-2 hover:bg-raised"
                            >
                              📁 {f}
                            </button>
                          ))}
                        <input
                          placeholder="New folder — ↵ to move"
                          onKeyDown={(e) => {
                            if (e.key === "Enter" && e.currentTarget.value.trim())
                              setFolder(t.id, e.currentTarget.value.trim());
                            if (e.key === "Escape") setMovePicker(null);
                          }}
                          className="mt-1 w-full rounded border border-line-strong bg-transparent px-2 py-1 text-xs outline-none placeholder:text-ink-3"
                        />
                      </div>
                    )}
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
