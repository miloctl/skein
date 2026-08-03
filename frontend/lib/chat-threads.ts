import { api } from "@/lib/api";

export type ChatThread = {
  id: string;
  title: string;
  folder: string;
  engagement_id: number | null;
  updated_at: string;
};

/** Single-flight chat list, shared by ChatSidebar and ThreadTitle: one
 *  "skein-chat-activity" event costs ONE /api/chats request, however many
 *  consumers listen. Same shape as authConfig() in lib/auth.ts — a failed
 *  read is not cached, so the next call retries. */
let cache: Promise<ChatThread[]> | null = null;

export function chatThreads(): Promise<ChatThread[]> {
  if (!cache) {
    const attempt = api<ChatThread[]>("/api/chats").catch((e) => {
      if (cache === attempt) cache = null;
      throw e;
    });
    cache = attempt;
  }
  return cache;
}

if (typeof window !== "undefined") {
  // These listeners register at import time, BEFORE any consumer's
  // effect-registered listener for the same events — listeners fire in
  // registration order, so a consumer reacting to the event always
  // refetches, never re-reads the stale promise.
  window.addEventListener("skein-chat-activity", () => {
    cache = null;
  });
  // identity switch (see lib/api.ts): a cached list belongs to one identity
  window.addEventListener("storage", () => {
    cache = null;
  });
}
