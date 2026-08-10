"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { actionError, api, getUser, loadError as describeLoadError } from "@/lib/api";
import { reportStatus } from "@/lib/status";
import { chatThreads, type ChatThread } from "@/lib/chat-threads";
import { copyText } from "@/lib/clipboard";

type EngagementRow = { id: number; name: string; status: string };

type StoredMessage = { role: "user" | "assistant"; content: string };

// one open panel at a time — dual menus are impossible by construction
type Menu =
  | { kind: "sidebar" }
  | { kind: "thread" | "move" | "rename" | "link"; id: string }
  | null;

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
  collapsed = false,
  mobileOpen = false,
  onMobileClose,
  threadId,
  onOpen,
  onNew,
}: {
  collapsed?: boolean;
  mobileOpen?: boolean;
  onMobileClose?: () => void;
  threadId: string;
  onOpen: (id: string) => void;
  onNew: () => void;
}) {
  const [threads, setThreads] = useState<ChatThread[]>([]);
  const [folders, setFolders] = useState<string[]>([]);
  const [foldersError, setFoldersError] = useState("");
  const [engagements, setEngagements] = useState<EngagementRow[] | null>(null);
  const [engagementsError, setEngagementsError] = useState("");
  const [dropTarget, setDropTarget] = useState<string | null>(null);
  const [creatingFolder, setCreatingFolder] = useState(false);
  const [menu, setMenu] = useState<Menu>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [selectMode, setSelectMode] = useState(false);
  const [selChats, setSelChats] = useState<Set<string>>(new Set());
  const [selFolders, setSelFolders] = useState<Set<string>>(new Set());
  const [loadError, setLoadError] = useState("");
  const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null);
  const [confirmingBulk, setConfirmingBulk] = useState(false);

  const load = useCallback(() => {
    // shared single-flight list (lib/chat-threads.ts) — ThreadTitle reads
    // the same fetch, so each activity event costs one request, not two
    chatThreads()
      .then((rows) => {
        setThreads(rows);
        setLoadError("");
      })
      .catch((e) => setLoadError(describeLoadError(e)));
    api<string[]>("/api/chats/folders")
      .then((f) => {
        setFolders(f);
        setFoldersError("");
      })
      .catch((e) => setFoldersError(describeLoadError(e)));
    api<EngagementRow[]>("/api/engagements")
      .then((rows) => {
        setEngagements(rows.filter((e) => e.status !== "closed"));
        setEngagementsError("");
      })
      .catch((e) => {
        // a served 4xx/5xx is not an unreachable backend — loadError says
        // which, instead of sending the reader to check a running server
        setEngagements(null);
        setEngagementsError(describeLoadError(e));
      });
  }, []);

  useEffect(() => {
    load();
    window.addEventListener("skein-chat-activity", load);
    return () => window.removeEventListener("skein-chat-activity", load);
  }, [load]);

  // after a mutation, announce rather than call load(): the event drops the
  // shared chatThreads() promise (lib/chat-threads.ts) before any listener
  // runs — a bare load() would re-read the pre-mutation list — and it also
  // refreshes ThreadTitle's h1, which load() alone never did
  const announce = () => window.dispatchEvent(new Event("skein-chat-activity"));

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

  useEffect(() => {
    if (!selectMode) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") exitSelect();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [selectMode]);

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
    }).catch((e) => reportStatus(actionError(e)));
    announce();
  };

  const setEngagement = async (id: string, engagementId: number) => {
    closeMenu(id);
    await api(`/api/chats/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ engagement_id: engagementId }),
    }).catch((e) => reportStatus(actionError(e)));
    announce();
  };

  const createAndLink = async (id: string, name: string) => {
    // snap to an existing open engagement first — the backend refuses
    // duplicate names, and retyping one should link, not error
    const existing = (engagements ?? []).find(
      (e) => e.name.toLowerCase() === name.toLowerCase(),
    );
    if (existing) return setEngagement(id, existing.id);
    try {
      const made = await api<EngagementRow>("/api/engagements", {
        method: "POST",
        body: JSON.stringify({ name }),
      });
      setEngagements((cur) => (cur ? [...cur, made] : [made]));
      await setEngagement(id, made.id);
    } catch (e) {
      reportStatus(actionError(e));
      throw e; // the caller restores the typed name on failure
    }
  };

  const setFolder = async (id: string, folder: string) => {
    closeMenu(id);
    await api(`/api/chats/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ folder }),
    }).catch((e) => reportStatus(actionError(e)));
    announce();
  };

  const renameTo = async (id: string, title: string) => {
    closeMenu(id);
    if (!title.trim()) return;
    await api(`/api/chats/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ title: title.trim() }),
    }).catch((e) => reportStatus(actionError(e)));
    announce();
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
      if (!(await copyText(md))) throw new Error("cannot copy here — select the text and copy manually");
      setCopied(t.id);
      setTimeout(() => {
        // identity-guarded: the 1200ms timer must never clobber a newer menu/toast
        setCopied((c) => (c === t.id ? null : c));
        setMenu((m) => (m && m.kind === "thread" && m.id === t.id ? null : m));
      }, 1200);
    } catch (e) {
      reportStatus(actionError(e));
    }
  };

  const remove = async (t: ChatThread) => {
    closeMenu();
    setConfirmingDelete(null);
    try {
      await api(`/api/chats/${t.id}`, { method: "DELETE" });
      if (t.id === threadId) onNew();
      announce();
    } catch (e) {
      reportStatus(actionError(e));
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
      reportStatus(actionError(e));
      // prune what succeeded so a retry never re-deletes ghosts
      setSelChats((s) => new Set([...s].filter((id) => !doneChats.has(id))));
      setSelFolders((s) => new Set([...s].filter((n) => !doneFolders.has(n))));
    } finally {
      if (activeSelected && doneChats.has(threadId)) onNew();
      announce();
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
    }).catch((err) => reportStatus(actionError(err)));
    announce();
  };

  // union with the threads' own folder fields: rendering only the fetched
  // list made every filed chat vanish when the folders fetch failed — the
  // t.folder === folder filter below matched no group. Deriving from the
  // threads means that failure costs only EMPTY folders.
  const filed = [...new Set(threads.map((t) => t.folder).filter(Boolean))]
    .filter((f) => !folders.includes(f))
    .sort();
  const groups = ["", ...folders, ...filed];
  const asideRef = useRef<HTMLElement>(null);
  useEffect(() => {
    // a panel over the page must take focus, or Tab walks the page behind it
    if (mobileOpen) asideRef.current?.querySelector("button")?.focus();
  }, [mobileOpen]);

  return (
    <aside
      id="chat-list"
      {...(mobileOpen
        ? { role: "dialog" as const, "aria-modal": true, "aria-label": "Chats" }
        : {})}
      ref={asideRef}
      onKeyDown={(e) => {
        if (!mobileOpen) return;
        if (e.key === "Escape") onMobileClose?.();
        if (e.key === "Tab") {
          // aria-modal above PROMISES focus stays inside; without this trap
          // Tab walks the page behind the drawer while the backdrop hides
          // where focus went (same trap as capture-palette.tsx)
          const focusables = asideRef.current?.querySelectorAll<HTMLElement>(
            "button:not([disabled]), a[href], input, [tabindex]",
          );
          if (!focusables || focusables.length === 0) return;
          const first = focusables[0];
          const last = focusables[focusables.length - 1];
          if (e.shiftKey && document.activeElement === first) {
            e.preventDefault();
            last.focus();
          } else if (!e.shiftKey && document.activeElement === last) {
            e.preventDefault();
            first.focus();
          }
        }
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
        "shrink-0 flex-col overflow-y-auto border-r p-3 md:static md:flex md:bg-transparent md:shadow-none " +
        "transition-[width,padding] duration-200 motion-reduce:transition-none " +
        (collapsed && !mobileOpen
          ? "md:invisible md:w-0 md:border-r-0 md:p-0 "
          : "md:visible md:w-64 ") +
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
                className="rounded bg-danger-solid px-2 py-1 font-medium text-white hover:opacity-90"
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
      {loadError && <p className="px-1 text-xs text-danger">{loadError}</p>}
      {/* suppressed while loadError shows: a dead backend fails both
          fetches with the same wording, and one line says it */}
      {foldersError && !loadError && (
        <p className="px-1 text-xs text-danger">{foldersError}</p>
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
                        icon="🧵"
                        label={
                          t.engagement_id
                            ? "Change engagement…"
                            : "Link to engagement…"
                        }
                        onClick={() => setMenu({ kind: "link", id: t.id })}
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
                  {menu?.kind === "link" && menu.id === t.id && !selectMode && (
                    <MenuPanel
                      label={`Link ${t.title} to an engagement`}
                      onClose={() => closeMenu(t.id)}
                    >
                      <p className="mb-1 px-1 font-mono text-[10px] font-medium uppercase tracking-[0.12em] text-ink-3">
                        Model spend in this chat counts toward
                      </p>
                      {/* the link now buys two things, so the menu says both:
                          spend attribution, and which engagement's memories
                          this conversation recalls (services/memory.py) */}
                      <p className="mb-1.5 px-1 text-[10px] text-ink-3">
                        A linked chat also recalls that engagement&apos;s own
                        memories. Filing one back is a proposal a person
                        approves.
                      </p>
                      {t.engagement_id != null && (
                        <button
                          onClick={() => setEngagement(t.id, 0)}
                          className="block w-full rounded px-2 py-1 text-left text-xs text-ink-2 hover:bg-raised"
                        >
                          ⊘ No engagement
                        </button>
                      )}
                      {(engagements ?? [])
                        .filter((e) => e.id !== t.engagement_id)
                        .map((e) => (
                          <button
                            key={e.id}
                            onClick={() => setEngagement(t.id, e.id)}
                            className="block w-full truncate rounded px-2 py-1 text-left text-xs text-ink-2 hover:bg-raised"
                          >
                            🧵 {e.name}
                          </button>
                        ))}
                      {engagements !== null && (
                        <input
                          autoFocus={engagements.length === 0}
                          name="link-new-engagement"
                          placeholder="New engagement — ↵ to create & link"
                          aria-label="Create an engagement and link this chat to it"
                          maxLength={120}
                          onKeyDown={(e) => {
                            const name = e.currentTarget.value.trim();
                            if (e.key === "Enter" && name) {
                              // clear synchronously: a second Enter during the
                              // POST round-trip would re-submit the same name
                              const box = e.currentTarget;
                              box.value = "";
                              createAndLink(t.id, name).catch(() => {
                                box.value = name; // a failed create must not eat the typed name
                              });
                            }
                            if (e.key === "Escape") closeMenu(t.id);
                          }}
                          className="mt-1 w-full rounded border border-line-strong bg-transparent px-2 py-1 text-xs outline-none placeholder:text-ink-3"
                        />
                      )}
                      {engagements === null && (
                        <p className="px-1 py-1 text-xs text-ink-3">
                          {engagementsError}
                        </p>
                      )}
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
