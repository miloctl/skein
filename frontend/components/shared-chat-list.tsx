"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { actionError, api, loadError } from "@/lib/api";
import {
  announceSharedChatActivity,
  isIdentityEvent,
  type SharedChatDetail,
  type SharedChatInvitation,
  type SharedChatSummary,
} from "@/lib/shared-chats";

const POLL_MS = 5_000;

export function SharedChatList({
  activeId,
  onOpen,
}: {
  activeId: string;
  onOpen: (id: string) => void;
}) {
  const [strong, setStrong] = useState<boolean | null>(null);
  const [rooms, setRooms] = useState<SharedChatSummary[]>([]);
  const [invitations, setInvitations] = useState<SharedChatInvitation[]>([]);
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const generation = useRef(0);
  const resolvedUser = useRef<string | null>(null);

  const load = useCallback(async () => {
    const current = ++generation.current;
    try {
      const identity = await api<{ user: string; strong: boolean }>("/api/whoami");
      if (current !== generation.current) return;
      if (resolvedUser.current !== null && resolvedUser.current !== identity.user) {
        setRooms([]);
        setInvitations([]);
        setError("");
      }
      resolvedUser.current = identity.user;
      setStrong(identity.strong);
      if (!identity.strong) {
        setRooms([]);
        setInvitations([]);
        setError("");
        return;
      }
      const [nextRooms, nextInvitations] = await Promise.all([
        api<SharedChatSummary[]>("/api/shared-chats", { cache: "no-store" }),
        api<SharedChatInvitation[]>("/api/shared-chats/invitations", {
          cache: "no-store",
        }),
      ]);
      if (current !== generation.current) return;
      setRooms(nextRooms);
      setInvitations(nextInvitations);
      setError("");
    } catch (caught) {
      // A failed LIST fetch is a load, not an action — same wording as the
      // sibling sidebar loads.
      if (current === generation.current) setError(loadError(caught));
    }
  }, []);

  useEffect(() => {
    queueMicrotask(load);
    const onActivity = () => load();
    const onIdentity = (event: Event) => {
      if (!isIdentityEvent(event)) return;
      generation.current += 1;
      setStrong(null);
      setRooms([]);
      setInvitations([]);
      setError("");
      load();
    };
    window.addEventListener("skein-shared-chat-activity", onActivity);
    window.addEventListener("storage", onIdentity);
    window.addEventListener("skein-identity-change", onIdentity);
    return () => {
      generation.current += 1;
      window.removeEventListener("skein-shared-chat-activity", onActivity);
      window.removeEventListener("storage", onIdentity);
      window.removeEventListener("skein-identity-change", onIdentity);
    };
  }, [load]);

  useEffect(() => {
    if (!strong) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState !== "hidden") load();
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [load, strong]);

  const create = async () => {
    const name = title.trim();
    if (!name || busy) return;
    setBusy(true);
    try {
      const room = await api<SharedChatDetail>("/api/shared-chats", {
        method: "POST",
        body: JSON.stringify({ title: name }),
      });
      setTitle("");
      setCreating(false);
      announceSharedChatActivity();
      onOpen(room.id);
    } catch (caught) {
      setError(actionError(caught));
    } finally {
      setBusy(false);
    }
  };

  const answer = async (invitation: SharedChatInvitation, accepted: boolean) => {
    if (busy) return;
    setBusy(true);
    try {
      const result = await api<SharedChatDetail | { status: string }>(
        `/api/shared-chats/invitations/${invitation.id}/${accepted ? "accept" : "decline"}`,
        { method: "POST" },
      );
      announceSharedChatActivity();
      if (accepted && "id" in result) onOpen(result.id);
    } catch (caught) {
      setError(actionError(caught));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section
      aria-labelledby="private-shared-chats-title"
      className="mb-3 mt-3 border-b border-line pb-3"
    >
      <div className="flex items-center justify-between gap-2 px-1">
        <h2
          id="private-shared-chats-title"
          className="font-mono text-[10px] font-medium uppercase tracking-[0.12em] text-ink-3"
        >
          Private shared chats
        </h2>
        {strong ? (
          <button
            type="button"
            aria-expanded={creating}
            onClick={() => setCreating((open) => !open)}
            className="min-h-11 rounded px-3 py-2 text-xs font-medium text-thread hover:bg-raised"
          >
            New private shared chat
          </button>
        ) : null}
      </div>

      {strong === null && !error ? (
        <p className="px-1 py-2 text-xs text-ink-3">Checking private chat access…</p>
      ) : strong === false ? (
        <div className="mt-2 rounded-lg bg-raised px-2 py-2 text-xs text-ink-2">
          <p>Private shared chats require deployment sign-in or a personal API key.</p>
          <Link href="/settings#settings-you" className="mt-1 inline-block font-medium text-thread underline">
            Open Settings &amp; access
          </Link>
        </div>
      ) : strong ? (
        <div className="mt-6">
          {creating ? (
            <div className="mt-2 space-y-2 rounded-lg border border-line bg-card p-2">
              <label className="block text-xs text-ink-3">
                Private shared chat title
                <input
                  autoFocus
                  value={title}
                  maxLength={60}
                  onChange={(event) => setTitle(event.target.value)}
                  onKeyDown={(event) => {
                    // isComposing: Enter that commits an IME conversion must
                    // not create the chat with a half-composed title.
                    if (event.key === "Enter" && !event.nativeEvent.isComposing) create();
                    if (event.key === "Escape") setCreating(false);
                  }}
                  className="mt-1 w-full rounded-lg border border-line-strong bg-transparent px-2 py-1.5 text-sm text-ink outline-none focus:border-thread-solid"
                />
              </label>
              <div className="flex gap-2">
                <button
                  type="button"
                  disabled={busy || !title.trim()}
                  onClick={create}
                  className="min-h-9 rounded-lg bg-thread-solid px-2.5 py-1 text-xs font-medium text-white disabled:opacity-40"
                >
                  Create private shared chat
                </button>
                <button
                  type="button"
                  onClick={() => setCreating(false)}
                  className="min-h-9 rounded-lg px-2.5 py-1 text-xs text-ink-2 hover:bg-raised"
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : null}

          {invitations.length > 0 ? (
            <ul className="mt-2 space-y-2">
              {invitations.map((invitation) => (
                <li key={invitation.id} className="rounded-lg border border-thread/30 bg-thread/5 p-2 text-xs">
                  <p className="break-all font-medium text-ink">
                    Private chat invitation from {invitation.invited_by}
                  </p>
                  <p className="mt-1 text-ink-2">
                    If you accept, you can read every earlier message in that chat.
                  </p>
                  <div className="mt-2 flex gap-2">
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => answer(invitation, true)}
                      className="min-h-9 break-all rounded bg-thread-solid px-2 py-1 font-medium text-white"
                    >
                      Accept invitation from {invitation.invited_by}
                    </button>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => answer(invitation, false)}
                      className="min-h-9 break-all rounded bg-raised px-2 py-1 text-ink-2"
                    >
                      Decline invitation from {invitation.invited_by}
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          ) : null}

          {rooms.length === 0 && invitations.length === 0 && !error ? (
            <p className="px-1 py-2 text-xs text-ink-3">No private shared chats yet.</p>
          ) : (
            <ul className="mt-2 space-y-1">
              {rooms.map((room) => {
                const unread = Number(room.unread_count) || 0;
                const participants = Number(room.member_count) || 0;
                return (
                  <li key={room.id}>
                    <button
                      type="button"
                      aria-label={`Open ${room.title}${unread ? `, ${unread} unread` : ""}`}
                      onClick={() => onOpen(room.id)}
                      className={
                        "w-full rounded-lg px-2 py-1.5 text-left text-sm " +
                        (room.id === activeId
                          ? "bg-thread/10 font-medium text-ink"
                          : "text-ink-2 hover:bg-raised")
                      }
                    >
                      <span className="flex items-center justify-between gap-2">
                        <span className="min-w-0 truncate">{room.title}</span>
                        {unread ? (
                          <span className="rounded-full bg-thread-solid px-1.5 py-px text-[10px] text-white">
                            {unread}
                          </span>
                        ) : null}
                      </span>
                      <span className="block text-[10px] font-normal text-ink-3">
                        {participants} {participants === 1 ? "participant" : "participants"}
                        {room.archived_at ? " · archived" : ""}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      ) : null}
      {error ? (
        <p role="alert" className="mt-2 px-1 text-xs text-danger">
          {error}
        </p>
      ) : null}
    </section>
  );
}
