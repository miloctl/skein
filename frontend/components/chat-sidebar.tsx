"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";

import { api, getUser } from "@/lib/api";
import { copyText } from "@/lib/clipboard";

export type ChatThread = {
  id: string;
  title: string;
  folder: string;
  updated_at: string;
};

type StoredMessage = { role: "user" | "assistant"; content: string };

// one open panel at a time — dual menus are impossible by construction
type Menu =
  | { kind: "sidebar" }
  | { kind: "thread" | "move" | "rename"; id: string }
  | null;

const COLLAPSE_KEY = "skein-chat-sidebar-collapsed";

/** Floating disclosure panel: focuses its first control, closes on Escape.
 *  Deliberately NOT role="menu" — plain buttons with Tab-through. */
function MenuPanel({
  label,
  onClose,
  children,
}: {
  label: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.querySelector<HTMLElement>("button, input")?.focus();
  }, []);
  return (
    <div
      ref={ref}
      data-menu
      role="group"
      aria-label={label}
      onBlur={(e) => {
        // Tab-out closes: pointerdown/Escape alone left it floating
        if (!e.currentTarget.contains(e.relatedTarget as Node)) onClose();
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation();
          onClose();
        }
      }}
      className="absolute left-0 right-0 top-full z-10 mt-1 rounded-xl border border-line bg-card p-1 shadow-float"
    >
      {children}
    </div>
  );
}

function MenuItem({
  icon,
  label,
  danger,
  onClick,
}: {
  icon: string;
  label: string;
  danger?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={
        "block w-full rounded px-2 py-1.5 text-left text-xs hover:bg-raised " +
        (danger ? "text-danger" : "text-ink-2")
      }
    >
      <span aria-hidden>{icon}</span> {label}
    </button>
  );
}

export function ChatSidebar({
  mobileOpen = false,
  onMobileClose,
  threadId,
  onOpen,
  onNew,
}: {
  mobileOpen?: boolean;
  onMobileClose?: () => void;
  threadId: string;
  onOpen: (id: string) => void;
  onNew: () => void;
}) {
  const [threads, setThreads] = useState<ChatThread[]>([]);
  const [folders, setFolders] = useState<string[]>([]);
  const [dropTarget, setDropTarget] = useState<string | null>(null);
  const [creatingFolder, setCreatingFolder] = useState(false);
  const [menu, setMenu] = useState<Menu>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [selectMode, setSelectMode] = useState(false);
  const [selChats, setSelChats] = useState<Set<string>>(new Set());
  const [selFolders, setSelFolders] = useState<Set<string>>(new Set());
  const [loadError, setLoadError] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null);
  const [confirmingBulk, setConfirmingBulk] = useState(false);
  const expandRail = useRef<HTMLButtonElement>(null);

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
      if (collapsed) localStorage.removeItem(COLLAPSE_KEY);
      else localStorage.setItem(COLLAPSE_KEY, "1");
    } catch {}
    window.dispatchEvent(new Event("storage"));
    if (!collapsed) setTimeout(() => expandRail.current?.focus(), 0);
  };

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

  // outside-click closes any open panel (pointerdown: Safari doesn't focus
  // buttons on click, so focus-based closing would silently fail there)
  useEffect(() => {
    if (!menu) return;
    const onPointerDown = (e: PointerEvent) => {
      if (!(e.target as Element | null)?.closest("[data-menu]")) {
        setMenu(null);
        setConfirmingDelete(null);
      }
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [menu]);

  const exitSelect = () => {
    setSelectMode(false);
    setSelChats(new Set());
    setSelFolders(new Set());
    setConfirmingBulk(false);
  };

  // Escape leaves select mode
  useEffect(() => {
    if (!selectMode) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") exitSelect();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  });

  const refocusTrigger = (id: string) =>
    setTimeout(() => document.getElementById(`chat-menu-${id}`)?.focus(), 0);

  const closeMenu = (refocus?: string) => {
    setMenu(null);
    setConfirmingDelete(null); // dismissal must always disarm the danger step
    if (refocus) refocusTrigger(refocus);
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
    closeMenu(id);
    await api(`/api/chats/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ folder }),
    }).catch((e) => alert(String(e)));
    load();
  };

  const renameTo = async (id: string, title: string) => {
    closeMenu(id);
    if (!title.trim()) return;
    await api(`/api/chats/${id}`, {
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
      if (!(await copyText(md))) throw new Error("couldn't copy — select and copy manually");
      setCopied(t.id);
      setTimeout(() => {
        // identity-guarded: never clobber a newer menu/toast (review fix)
        setCopied((c) => (c === t.id ? null : c));
        setMenu((m) => (m && m.kind === "thread" && m.id === t.id ? null : m));
      }, 1200);
    } catch (e) {
      alert(String(e));
    }
  };

  const remove = async (t: ChatThread) => {
    closeMenu();
    setConfirmingDelete(null);
    try {
      await api(`/api/chats/${t.id}`, { method: "DELETE" });
      if (t.id === threadId) onNew();
      load();
    } catch (e) {
      alert(String(e));
    }
  };

  const enterSelect = (seedChat?: string) => {
    setMenu(null);
    setConfirmingBulk(false);
    setSelectMode(true);
    setSelChats(new Set(seedChat ? [seedChat] : []));
    setSelFolders(new Set());
  };

  const toggleSet = (set: Set<string>, key: string) => {
    const next = new Set(set);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    return next;
  };

  const deleteSelected = async () => {
    if (selChats.size + selFolders.size === 0) return;
    setConfirmingBulk(false);
    const activeSelected = selChats.has(threadId);
    const doneChats = new Set<string>();
    const doneFolders = new Set<string>();
    try {
      for (const id of selChats) {
        await api(`/api/chats/${id}`, { method: "DELETE" });
        doneChats.add(id);
      }
      for (const name of selFolders) {
        await api(`/api/chats/folders/${encodeURIComponent(name)}`, {
          method: "DELETE",
        });
        doneFolders.add(name);
      }
      exitSelect();
    } catch (e) {
      alert(String(e));
      // prune what succeeded so a retry never re-deletes ghosts (review fix)
      setSelChats((s) => new Set([...s].filter((id) => !doneChats.has(id))));
      setSelFolders((s) => new Set([...s].filter((n) => !doneFolders.has(n))));
    } finally {
      if (activeSelected && doneChats.has(threadId)) onNew();
      load();
    }
  };

  const dropInto = async (e: React.DragEvent, folder: string) => {
    e.preventDefault();
    e.stopPropagation();
    setDropTarget(null);
    setMenu(null);
    const id = e.dataTransfer.getData("text/plain");
    if (!id || !threads.some((t) => t.id === id)) return; // stray payloads
    await api(`/api/chats/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ folder }),
    }).catch((err) => alert(String(err)));
    load();
  };

  const groups = ["", ...folders];
  const asideRef = useRef<HTMLElement>(null);
  useEffect(() => {
    // a panel over the page must take focus, or Tab walks the page behind it
    if (mobileOpen) asideRef.current?.querySelector("button")?.focus();
  }, [mobileOpen]);

  if (collapsed && !mobileOpen) {
    return (
      <button
        ref={expandRail}
        onClick={toggleCollapsed}
        title="Show chats"
        aria-label="Show chat sidebar"
        className="hidden h-full w-6 shrink-0 border-r border-line text-xs text-ink-3 hover:bg-raised hover:text-ink md:block"
      >
        »
      </button>
    );
  }

  return (
    <aside
      id="chat-list"
      {...(mobileOpen
        ? { role: "dialog" as const, "aria-modal": true, "aria-label": "Chats" }
        : {})}
      ref={asideRef}
      onKeyDown={(e) => {
        if (mobileOpen && e.key === "Escape") onMobileClose?.();
      }}
      onDragOver={(e) => {
        e.preventDefault();
        setDropTarget("");
      }}
      onDrop={(e) => dropInto(e, "")}
      onDragLeave={() => setDropTarget(null)}
      className={
        (mobileOpen
          ? "fixed bottom-0 left-0 top-[calc(var(--nav-h)+var(--selvage-h,2px))] z-30 flex w-72 bg-page shadow-float "
          : "hidden ") +
        "shrink-0 flex-col overflow-y-auto border-r p-3 md:static md:flex md:w-64 md:bg-transparent md:shadow-none " +
        (dropTarget === "" ? "border-thread-solid bg-thread/5" : "border-line")
      }
    >
      {selectMode ? (
        <div className="mb-3 flex items-center gap-1.5 rounded-lg border border-line bg-raised px-2 py-1.5 text-xs">
          <span className="flex-1 font-medium">
            {[
              selChats.size ? `${selChats.size} chat${selChats.size > 1 ? "s" : ""}` : "",
              selFolders.size
                ? `${selFolders.size} folder${selFolders.size > 1 ? "s" : ""}`
                : "",
            ]
              .filter(Boolean)
              .join(" · ") || "0 selected"}
          </span>
          {confirmingBulk ? (
            <>
              <button
                autoFocus
                onClick={deleteSelected}
                className="rounded bg-danger px-2 py-1 font-medium text-white hover:opacity-90"
              >
                {selChats.size
                  ? "Really delete — transcripts gone"
                  : "Really delete — chats stay, unfiled"}
              </button>
              <button
                onClick={() => setConfirmingBulk(false)}
                className="rounded px-2 py-1 text-ink-2 hover:bg-line"
              >
                Keep
              </button>
            </>
          ) : (
            <button
              onClick={() => setConfirmingBulk(true)}
              disabled={selChats.size + selFolders.size === 0}
              className="rounded bg-danger/15 px-2 py-1 font-medium text-danger hover:bg-danger/20 disabled:opacity-40"
            >
              Delete…
            </button>
          )}
          <button
            onClick={exitSelect}
            className="rounded px-2 py-1 text-ink-2 hover:bg-line"
          >
            Cancel
          </button>
        </div>
      ) : (
        <div data-menu className="relative mb-3">
          <div className="flex gap-1.5">
            <button
              onClick={onNew}
              className="flex-1 rounded-lg bg-thread-solid px-3 py-1.5 text-sm font-medium text-white hover:opacity-90"
            >
              + New chat
            </button>
            <button
              onClick={() =>
                setMenu(menu?.kind === "sidebar" ? null : { kind: "sidebar" })
              }
              title="More"
              aria-label="Chat list options"
              aria-expanded={menu?.kind === "sidebar"}
              className="rounded-lg border border-line-strong px-2.5 py-1.5 text-sm text-ink-2 hover:bg-raised"
            >
              ⋯
            </button>
            <button
              onClick={() => (mobileOpen ? onMobileClose?.() : toggleCollapsed())}
              title={mobileOpen ? "Close chat list" : "Collapse sidebar"}
              aria-label={mobileOpen ? "Close chat list" : "Collapse sidebar"}
              className="rounded-lg border border-line-strong px-2 py-1.5 text-sm text-ink-2 hover:bg-raised"
            >
              «
            </button>
          </div>
          {menu?.kind === "sidebar" && (
            <MenuPanel label="Chat list options" onClose={() => setMenu(null)}>
              <MenuItem
                icon="📁"
                label="New folder"
                onClick={() => {
                  setMenu(null);
                  setCreatingFolder(true);
                }}
              />
              <MenuItem icon="☑️" label="Select…" onClick={() => enterSelect()} />
            </MenuPanel>
          )}
        </div>
      )}
      {creatingFolder && !selectMode && (
        <input
          autoFocus
          name="new-folder"
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
          Your chats appear here after the first message — rename them or file
          them into folders to keep threads you return to.
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
                  className="h-4 w-4 p-1"
                />
              )}
              <span
                className={"flex-1" + (selectMode ? " cursor-pointer" : "")}
                onClick={
                  selectMode
                    ? () => setSelFolders(toggleSet(selFolders, folder))
                    : undefined
                }
              >
                📁 {folder}
              </span>
            </p>
          )}
          {folder && threads.filter((t) => t.folder === folder).length === 0 && (
            <p className="px-2 pb-1 text-[11px] italic text-ink-3">
              drag chats here
            </p>
          )}
          <ul className="space-y-0.5">
            {threads
              .filter((t) => t.folder === folder)
              .map((t) => (
                <li
                  key={t.id}
                  id={`chat-${t.id}`}
                  data-menu
                  className="group relative"
                >
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
                          : onOpen(t.id)
                      }
                      draggable={!selectMode}
                      onDragStart={(e) =>
                        e.dataTransfer.setData("text/plain", t.id)
                      }
                      className={
                        "min-w-0 flex-1 truncate rounded-lg px-2 py-1.5 text-left text-sm transition-colors " +
                        (selectMode
                          ? ""
                          : "cursor-grab active:cursor-grabbing pr-8 ") +
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
                      id={`chat-menu-${t.id}`}
                      onClick={() =>
                        setMenu(
                          menu?.kind === "thread" && menu.id === t.id
                            ? null
                            : { kind: "thread", id: t.id },
                        )
                      }
                      title="More"
                      aria-label={`More actions for ${t.title}`}
                      aria-expanded={menu?.kind === "thread" && menu.id === t.id}
                      className={
                        "absolute right-1 top-1/2 -translate-y-1/2 rounded px-2 py-1 text-sm text-ink-3 hover:bg-line group-focus-within:block group-hover:block " +
                        ((menu?.kind !== "sidebar" &&
                          menu?.id === t.id) ||
                        t.id === threadId
                          ? "block"
                          : "hidden [@media(any-pointer:coarse)]:block")
                      }
                    >
                      ⋯
                    </button>
                  )}
                  {menu?.kind === "thread" && menu.id === t.id && !selectMode && (
                    <MenuPanel
                      label={`Actions for ${t.title}`}
                      onClose={() => closeMenu(t.id)}
                    >
                      <MenuItem
                        icon="✏️"
                        label="Rename"
                        onClick={() => setMenu({ kind: "rename", id: t.id })}
                      />
                      <MenuItem
                        icon="📁"
                        label="Move to folder…"
                        onClick={() => setMenu({ kind: "move", id: t.id })}
                      />
                      <MenuItem
                        icon={copied === t.id ? "✓" : "📋"}
                        label={copied === t.id ? "Copied" : "Copy as Markdown"}
                        onClick={() => copyTranscript(t)}
                      />
                      <MenuItem
                        icon="☑️"
                        label="Select…"
                        onClick={() => enterSelect(t.id)}
                      />
                      {confirmingDelete === t.id ? (
                        <>
                          <MenuItem
                            icon="🗑"
                            label="Delete for good — transcript too"
                            danger
                            onClick={() => remove(t)}
                          />
                          <MenuItem
                            icon="↩"
                            label="Keep it"
                            onClick={() => setConfirmingDelete(null)}
                          />
                        </>
                      ) : (
                        <MenuItem
                          icon="🗑"
                          label="Delete…"
                          danger
                          onClick={() => setConfirmingDelete(t.id)}
                        />
                      )}
                    </MenuPanel>
                  )}
                  {menu?.kind === "rename" && menu.id === t.id && (
                    <MenuPanel
                      label={`Rename ${t.title}`}
                      onClose={() => closeMenu(t.id)}
                    >
                      <input
                        autoFocus
                        name="rename-chat"
                        defaultValue={t.title}
                        aria-label="New chat name"
                        onFocus={(e) => e.currentTarget.select()}
                        onKeyDown={(e) => {
                          if (e.key === "Enter")
                            renameTo(t.id, e.currentTarget.value);
                        }}
                        className="w-full rounded border border-thread-solid bg-transparent px-2 py-1 text-xs outline-none"
                      />
                    </MenuPanel>
                  )}
                  {menu?.kind === "move" && menu.id === t.id && !selectMode && (
                    <MenuPanel
                      label={`Move ${t.title}`}
                      onClose={() => closeMenu(t.id)}
                    >
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
                        autoFocus={folders.length === 0 && !t.folder}
                        name="move-to-new-folder"
                        placeholder="New folder — ↵ to move"
                        aria-label="Move to a new folder"
                        onKeyDown={(e) => {
                          if (e.key === "Enter" && e.currentTarget.value.trim())
                            setFolder(t.id, e.currentTarget.value.trim());
                          if (e.key === "Escape") closeMenu(t.id);
                        }}
                        className="mt-1 w-full rounded border border-line-strong bg-transparent px-2 py-1 text-xs outline-none placeholder:text-ink-3"
                      />
                    </MenuPanel>
                  )}
                </li>
              ))}
          </ul>
        </div>
      ))}
    </aside>
  );
}
