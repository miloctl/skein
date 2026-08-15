"use client";

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";

import { RuntimeProvider } from "../runtime-provider";
import { ChatSidebar } from "@/components/chat-sidebar";
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
  const [missing, setMissing] = useState(false);
  const recoveryRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    try {
      sessionStorage.setItem(LAST_KEY, threadId);
    } catch {}
  }, [threadId]);

  useEffect(() => {
    const onMissing = (event: Event) => {
      const id = (event as CustomEvent<{ threadId?: string }>).detail?.threadId;
      if (id !== threadId) return;
      try {
        sessionStorage.removeItem(LAST_KEY);
      } catch {}
      setMissing(true);
    };
    window.addEventListener("skein-chat-missing", onMissing);
    return () => window.removeEventListener("skein-chat-missing", onMissing);
  }, [threadId]);

  useEffect(() => {
    if (missing) recoveryRef.current?.focus();
  }, [missing]);

  const open = (id: string) => {
    setMissing(false);
    if (id === threadId) return;
    setActivePersona(null); // persona mode is per-conversation
    setThreadId(id);
    document.getElementById(`chat-${id}`)?.scrollIntoView({ block: "nearest" });
  };

  const collapsed = useSyncExternalStore(
    subscribeChatLayout,
    getSidebarCollapsed,
    serverSidebarCollapsed,
  );
  const [mobileChats, setMobileChats] = useState(false);
  const chatsBtnRef = useRef<HTMLButtonElement>(null);
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
    setMissing(false);
    setActivePersona(null);
    setThreadId(newId());
  };

  return (
    <div className="flex h-[calc(100dvh-var(--nav-h)-var(--selvage-h,2px))] w-full">
      <ChatSidebar
          collapsed={collapsed}
          mobileOpen={mobileChats}
          onMobileClose={closeChats}
          threadId={threadId}
          onOpen={(id) => {
            setMobileChats(false);
            open(id);
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
          className="fixed inset-0 top-[calc(var(--nav-h)+var(--selvage-h,2px))] z-20 bg-black/30 md:hidden"
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
          <ThreadTitle threadId={threadId} />
        </div>
        {missing ? (
          <div
            role="alert"
            className="flex shrink-0 items-center justify-between gap-3 border-b border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger sm:px-4"
          >
            <p>Message not sent. This chat is not available.</p>
            <button
              ref={recoveryRef}
              type="button"
              onClick={startNew}
              className="shrink-0 rounded-lg border border-danger/40 px-2.5 py-1 font-medium hover:bg-danger/10"
            >
              New chat
            </button>
          </div>
        ) : (
          <RuntimeProvider key={threadId} threadId={threadId}>
            <Thread />
          </RuntimeProvider>
        )}
      </main>
    </div>
  );
}
