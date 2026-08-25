"use client";

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";

import { RuntimeProvider } from "../runtime-provider";
import { ChatSidebar } from "@/components/chat-sidebar";
import { SharedChat } from "@/components/shared-chat";
import { ThreadTitle } from "@/components/thread-title";
import { Thread } from "@/components/thread";
import { setActivePersona } from "@/lib/persona";
import {
  getSidebarCollapsed,
  serverSidebarCollapsed,
  subscribeChatLayout,
  toggleSidebar,
} from "@/lib/chat-layout";

const LAST_KEY = "skein-last-chat";

function newId() {
  // crypto.randomUUID is secure-context-only — absent when Skein is served
  // over plain http. getRandomValues is not.
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

type ChatSelection = { id: string; kind: "solo" | "shared" };
type MissingCause = "" | "send" | "access";

function initialThread(): ChatSelection {
  if (typeof window !== "undefined") {
    const shared = new URLSearchParams(window.location.search).get("shared") ?? "";
    if (/^shared-[A-Za-z0-9_-]{1,57}$/.test(shared)) {
      return { id: shared, kind: "shared" };
    }
  }
  // Reopen where you left off. The old value was a bare solo-thread id, so a
  // failed JSON parse preserves it instead of discarding every existing tab.
  try {
    const saved = sessionStorage.getItem(LAST_KEY);
    if (!saved) return { id: newId(), kind: "solo" };
    try {
      const parsed = JSON.parse(saved) as ChatSelection;
      if (
        parsed &&
        typeof parsed.id === "string" &&
        (parsed.kind === "solo" || parsed.kind === "shared")
      )
        return parsed;
    } catch {
      return { id: saved, kind: "solo" };
    }
    return { id: newId(), kind: "solo" };
  } catch {
    return { id: newId(), kind: "solo" };
  }
}

export default function ChatPage() {
  const [selection, setSelection] = useState<ChatSelection>(initialThread);
  const [sharedTitle, setSharedTitle] = useState("Private shared chat");
  const threadId = selection.id;
  const [missing, setMissing] = useState<MissingCause>("");
  const recoveryRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    try {
      sessionStorage.setItem(LAST_KEY, JSON.stringify(selection));
    } catch {}
    if (selection.kind === "shared") {
      const current = new URLSearchParams(window.location.search).get("shared");
      if (current !== selection.id) {
        window.history.replaceState(
          {},
          "",
          `/chat?shared=${encodeURIComponent(selection.id)}`,
        );
      }
    } else {
      const params = new URLSearchParams(window.location.search);
      if (params.has("shared") || window.location.hash.startsWith("#shared-message-")) {
        window.history.replaceState({}, "", "/chat");
      }
    }
  }, [selection]);

  useEffect(() => {
    document.title =
      selection.kind === "shared" ? `${sharedTitle} — Skein` : "Chat — Skein";
  }, [selection.kind, sharedTitle]);

  useEffect(() => {
    const onMissing = (event: Event) => {
      const id = (event as CustomEvent<{ threadId?: string }>).detail?.threadId;
      if (id !== threadId) return;
      try {
        sessionStorage.removeItem(LAST_KEY);
      } catch {}
      setMissing("send");
    };
    window.addEventListener("skein-chat-missing", onMissing);
    return () => window.removeEventListener("skein-chat-missing", onMissing);
  }, [threadId]);

  useEffect(() => {
    if (missing) recoveryRef.current?.focus();
  }, [missing]);

  const open = (id: string, kind: "solo" | "shared" = "solo") => {
    setMissing("");
    window.history.replaceState(
      {},
      "",
      kind === "shared" ? `/chat?shared=${encodeURIComponent(id)}` : "/chat",
    );
    if (id === selection.id && kind === selection.kind) return;
    setActivePersona(null); // persona mode is per-conversation
    if (kind === "shared") setSharedTitle("Private shared chat");
    setSelection({ id, kind });
    document.getElementById(`chat-${id}`)?.scrollIntoView({ block: "nearest" });
  };

  const collapsed = useSyncExternalStore(
    subscribeChatLayout,
    getSidebarCollapsed,
    serverSidebarCollapsed,
  );
  const [mobileChats, setMobileChats] = useState(false);
  const chatsBtnRef = useRef<HTMLButtonElement>(null);
  const sharedUnavailable = useCallback(() => {
    window.history.replaceState({}, "", "/chat");
    try {
      sessionStorage.removeItem(LAST_KEY);
    } catch {}
    setSharedTitle("Private shared chat");
    setMissing("access");
  }, []);
  const closeChats = useCallback(() => {
    setMobileChats(false);
    setTimeout(() => chatsBtnRef.current?.focus(), 0);
  }, []);
  useEffect(() => {
    // crossing to >=md must drop the drawer state — it would force-open a
    // deliberately-collapsed desktop sidebar (and re-present on rotate back)
    const mq = window.matchMedia("(min-width: 768px)");
    const onChange = () => mq.matches && setMobileChats(false);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  const startNew = () => {
    window.history.replaceState({}, "", "/chat");
    setMissing("");
    setActivePersona(null);
    setSelection({ id: newId(), kind: "solo" });
  };

  return (
    <div className="flex h-[calc(100dvh-var(--nav-h)-var(--selvage-h,2px))] w-full overflow-hidden">
      <ChatSidebar
          collapsed={collapsed}
          mobileOpen={mobileChats}
          onMobileClose={closeChats}
          threadId={threadId}
          threadKind={selection.kind}
          onOpen={(id, kind = "solo") => {
            setMobileChats(false);
            open(id, kind);
          }}
          onNew={() => {
            setMobileChats(false);
            startNew();
          }}
        />
      {mobileChats && (
        <button
          aria-label="Close chat list"
          onClick={closeChats}
          // BELOW the header's z-10 (nav.tsx), with the drawer in chat-
          // sidebar.tsx. Both start under the header already, so they never
          // needed to outrank it — and above it they swallowed every header
          // popover that hangs down: page help, search results and the
          // identity menu all became unclickable with the drawer open, while
          // focus still moved into them. It only has to beat the thread,
          // which carries no z-index at all.
          className="fixed inset-0 top-[calc(var(--nav-h)+var(--selvage-h,2px))] z-1 bg-black/30 md:hidden"
        />
      )}
      <main
        id="content"
        tabIndex={-1}
        className="flex h-full min-h-0 min-w-0 flex-1 flex-col"
      >
        {/* one header bar, one control per breakpoint — the toggle lives
            OUTSIDE the sidebar, so it never has to teleport when it hides */}
        <div className="flex shrink-0 items-center gap-2 border-b border-line px-3 py-2 sm:px-4">
          <button
            ref={chatsBtnRef}
            onClick={() => setMobileChats(true)}
            aria-expanded={mobileChats}
            aria-controls="chat-list"
            className="flex min-h-9 shrink-0 items-center gap-1.5 rounded-lg border border-line-strong bg-raised px-2.5 py-1.5 text-xs text-ink-2 hover:bg-line hover:text-ink md:hidden"
          >
            <span aria-hidden>☰</span> Chats
          </button>
          <button
            onClick={toggleSidebar}
            aria-expanded={!collapsed}
            aria-controls="chat-list"
            title={collapsed ? "Show chat list" : "Hide chat list"}
            className="hidden min-h-9 shrink-0 items-center gap-1.5 rounded-lg border border-line-strong bg-raised px-2.5 py-1.5 text-xs text-ink-2 hover:bg-line hover:text-ink md:flex"
          >
            <span aria-hidden>☰</span> Chats
          </button>
          {selection.kind === "shared" ? (
            <p className="min-w-0 flex-1 truncate font-display text-sm font-semibold text-ink">
              {sharedTitle}
            </p>
          ) : (
            <ThreadTitle threadId={threadId} />
          )}
        </div>
        {missing ? (
          <div
            role="alert"
            className="flex shrink-0 items-center justify-between gap-3 border-b border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger sm:px-4"
          >
            <p>
              {missing === "send"
                ? "Message not sent. This chat is not available."
                : "This private shared chat is no longer available. Select another chat."}
            </p>
            <button
              ref={recoveryRef}
              type="button"
              onClick={startNew}
              className="shrink-0 rounded-lg border border-danger/40 px-2.5 py-1 font-medium hover:bg-danger/10"
            >
              New chat
            </button>
          </div>
        ) : selection.kind === "shared" ? (
          <SharedChat
            key={threadId}
            threadId={threadId}
            onTitle={setSharedTitle}
            onUnavailable={sharedUnavailable}
            onLeave={startNew}
          />
        ) : (
          <RuntimeProvider key={threadId} threadId={threadId}>
            <Thread />
          </RuntimeProvider>
        )}
      </main>
    </div>
  );
}
