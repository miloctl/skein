"use client";

import { useSyncExternalStore } from "react";

import { subscribeSession, trustedHeaderIdentity } from "@/lib/auth";

/** Shown on each personal surface (solo chats, attached files, memories,
 *  notices, your own activity) while the caller is a trusted-header name with
 *  no key. In that mode a name is whatever a caller types, so these records
 *  are only as private as the network. The server keeps working in that mode
 *  on purpose; a team that needs privacy runs api-key or oidc
 *  (docs/VISIBILITY.md). */
export function WeakIdentityNotice({ className = "" }: { className?: string }) {
  const weak = useSyncExternalStore(subscribeSession, trustedHeaderIdentity, () => false);
  if (!weak) return null;
  return (
    <p
      className={`rounded-lg border border-weld/40 bg-weld/10 px-3 py-2 text-xs text-weld ${className}`}
    >
      Anyone who can reach this server can pick your name and read this.{" "}
      <a href="/settings#settings-you" className="font-medium underline">
        Sign in with a key for privacy.
      </a>
    </p>
  );
}
