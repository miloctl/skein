"use client";

import { useEffect, useState } from "react";

import { RuntimeProvider } from "../runtime-provider";
import { ChatSidebar } from "@/components/chat-sidebar";
import { Thread } from "@/components/thread";
import { setActivePersona } from "@/lib/persona";

const LAST_KEY = "skein-last-chat";

function newId() {
  return crypto.randomUUID();
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

  const startNew = () => {
    setActivePersona(null);
    setThreadId(newId());
  };

  return (
    <div className="mx-auto flex h-[calc(100vh-3.5rem)] w-full max-w-6xl">
      <ChatSidebar threadId={threadId} onOpen={open} onNew={startNew} />
      <RuntimeProvider key={threadId} threadId={threadId}>
        <main className="mx-auto flex h-full w-full max-w-3xl flex-col">
          <Thread />
        </main>
      </RuntimeProvider>
    </div>
  );
}
