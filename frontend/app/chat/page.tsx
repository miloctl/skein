"use client";

import { useEffect, useState } from "react";

import { RuntimeProvider } from "../runtime-provider";
import { ChatSidebar } from "@/components/chat-sidebar";
import { Thread } from "@/components/thread";
import { setActivePersona } from "@/lib/persona";

const LAST_KEY = "skein-last-chat";

function newId() {
  // crypto.randomUUID is secure-context-only — absent over plain http on a
  // LAN IP, which is exactly how Skein is served. getRandomValues is not.
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

  useEffect(() => {
    try {
      sessionStorage.setItem(LAST_KEY, threadId);
    } catch {}
  }, [threadId]);

  const open = (id: string) => {
    if (id === threadId) return;
    setActivePersona(null); // persona mode is per-conversation
    setThreadId(id);
    document.getElementById(`chat-${id}`)?.scrollIntoView({ block: "nearest" });
  };

  const [mobileChats, setMobileChats] = useState(false);
  useEffect(() => {
    // crossing to >=md must drop the drawer state — it would force-open a
    // deliberately-collapsed desktop sidebar (and re-present on rotate back)
    const mq = window.matchMedia("(min-width: 768px)");
    const onChange = () => mq.matches && setMobileChats(false);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  const startNew = () => {
    setActivePersona(null);
    setThreadId(newId());
  };

  return (
    <div className="mx-auto flex h-[calc(100dvh-6.25rem-var(--selvage-h,2px))] w-full max-w-6xl md:h-[calc(100dvh-3.5rem-var(--selvage-h,2px))]">
      <ChatSidebar
        mobileOpen={mobileChats}
        onMobileClose={() => setMobileChats(false)}
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
          onClick={() => setMobileChats(false)}
          className="fixed inset-0 top-[calc(6.25rem+var(--selvage-h,2px))] z-20 bg-black/30 md:hidden"
        />
      )}
      <main className="mx-auto flex h-full w-full max-w-3xl flex-col">
        <button
          onClick={() => setMobileChats(true)}
          className="mx-4 mt-2 self-start rounded-lg border border-line-strong bg-raised px-2.5 py-1.5 text-xs text-ink-2 hover:bg-line md:hidden"
        >
          ☰ Chats
        </button>
        <RuntimeProvider key={threadId} threadId={threadId}>
          <Thread />
        </RuntimeProvider>
      </main>
    </div>
  );
}
