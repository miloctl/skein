"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { PersonInput } from "@/components/person-input";
import { actionError, api } from "@/lib/api";
import {
  announceSharedChatActivity,
  type BenchPersona,
  type SharedChatAgentRun,
  type SharedChatDetail,
  type SharedChatMessage,
} from "@/lib/shared-chats";
import { HASH_TARGET, useHashTarget } from "@/lib/hash-target";
import { timeAgo } from "@/lib/time";

const POLL_MS = 2_000;
const DETAIL_POLL_MS = 5_000;
const PAGE_SIZE = 1_000;

type AccessAction =
  | {
      kind: "role";
      person: string;
      role: "steward" | "member";
      trigger: HTMLButtonElement;
    }
  | { kind: "remove"; person: string; trigger: HTMLButtonElement }
  | {
      kind: "revoke";
      person: string;
      invitationId: number;
      trigger: HTMLButtonElement;
    }
  | { kind: "add-agent"; persona: BenchPersona; trigger: HTMLButtonElement }
  | { kind: "remove-agent"; person: string; trigger: HTMLButtonElement }
  | { kind: "archive"; trigger: HTMLButtonElement }
  | { kind: "leave"; trigger: HTMLButtonElement };

function accessActionCopy(action: AccessAction) {
  if (action.kind === "role") {
    return action.role === "steward"
      ? {
          message: `${action.person} can manage participants, invitations, the title, and archive state.`,
          label: `Confirm: make ${action.person} a steward`,
        }
      : {
          message: `${action.person} will lose steward controls.`,
          label: `Confirm: make ${action.person} a member`,
        };
  }
  if (action.kind === "remove") {
    return {
      message: `${action.person} will lose access to every message in this chat. Their messages stay attributed.`,
      label: `Confirm: remove ${action.person}`,
    };
  }
  if (action.kind === "revoke") {
    return {
      message: `${action.person} can no longer accept this invitation.`,
      label: `Confirm: revoke invitation for ${action.person}`,
    };
  }
  if (action.kind === "add-agent") {
    return {
      message: `${action.persona.name} can read this private chat history when a participant calls @${action.persona.slug}.`,
      label: `Add ${action.persona.name} to this private chat`,
    };
  }
  if (action.kind === "remove-agent") {
    return {
      message: `${action.person} cannot answer new calls. Existing messages stay attributed.`,
      label: `Confirm: remove ${action.person}`,
    };
  }
  if (action.kind === "archive") {
    return {
      message: "This chat will become read-only for every participant.",
      label: "Confirm: archive shared chat",
    };
  }
  return {
    message:
      "You will lose access to every message in this chat. You need a new invitation to return.",
    label: "Confirm: leave shared chat",
  };
}

function clientKey(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
}

function mergeMessages(
  current: SharedChatMessage[],
  incoming: SharedChatMessage[],
): SharedChatMessage[] {
  const rows = new Map(current.map((message) => [message.id, message]));
  for (const message of incoming) rows.set(message.id, message);
  return [...rows.values()].sort((a, b) => a.id - b.id);
}

function invokedAgents(message: string, agents: string[]): string[] {
  const found: string[] = [];
  let rest = message.trimStart();
  while (found.length < 4) {
    const match = rest.match(/^@([a-z0-9][a-z0-9-]{1,40})(?:\s+|$)/);
    if (!match || !agents.includes(match[1])) break;
    if (!found.includes(match[1])) found.push(match[1]);
    rest = rest.slice(match[0].length);
  }
  return found;
}

function addAgentMention(message: string, agent: string, agents: string[]): string {
  const current = message.trimStart();
  const invoked = invokedAgents(current, agents);
  if (invoked.includes(agent) || invoked.length >= 4) return message;
  let rest = current;
  for (const slug of invoked) {
    rest = rest.replace(new RegExp(`^@${slug}(?:\\s+|$)`), "");
  }
  const prefix = [...invoked, agent].map((slug) => `@${slug}`).join(" ");
  return rest ? `${prefix} ${rest}` : `${prefix} `;
}

function runMessage(run: SharedChatAgentRun): string {
  if (run.status === "pending") return `${run.agent} is waiting to respond.`;
  if (run.status === "running") return `${run.agent} is responding…`;
  if (run.status === "completion_unknown") {
    return `Skein could not determine whether ${run.agent} completed the response. Send a new @${run.agent} message if you still need it.`;
  }
  if (run.status === "refused") {
    if (run.error_code === "agent_unavailable") {
      return `${run.agent} is not available. A steward can remove it, or an administrator can reactivate it.`;
    }
    if (run.error_code === "audience_unavailable") {
      return `Participant access could not be refreshed. Send a new @${run.agent} message after access is restored.`;
    }
    return `The requester is no longer an active participant. Another participant must send a new @${run.agent} message.`;
  }
  return `${run.agent} could not complete the response. Send a new @${run.agent} message to try again.`;
}

export function SharedChat({
  threadId,
  onTitle,
  onUnavailable,
  onLeave,
}: {
  threadId: string;
  onTitle?: (title: string) => void;
  onUnavailable?: () => void;
  onLeave?: () => void;
}) {
  const [detail, setDetail] = useState<SharedChatDetail | null>(null);
  const [messages, setMessages] = useState<SharedChatMessage[]>([]);
  const [agentRuns, setAgentRuns] = useState<SharedChatAgentRun[]>([]);
  const [personas, setPersonas] = useState<BenchPersona[]>([]);
  const [engagements, setEngagements] = useState<Array<{ id: number; name: string }>>([]);
  const [agentDraft, setAgentDraft] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [hasOlder, setHasOlder] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [manageOpen, setManageOpen] = useState(false);
  const [inviteDraft, setInviteDraft] = useState("");
  const [inviteReview, setInviteReview] = useState("");
  const [titleDraft, setTitleDraft] = useState("");
  const [announcement, setAnnouncement] = useState("");
  const [accessAction, setAccessAction] = useState<AccessAction | null>(null);
  const latestId = useRef(0);
  const generation = useRef(0);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const participantsHeadingRef = useRef<HTMLHeadingElement>(null);
  const manageButtonRef = useRef<HTMLButtonElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const retryKey = useRef<{ message: string; key: string } | null>(null);

  useHashTarget(messages);

  const markRead = useCallback(
    (messageId: number) => {
      if (!messageId || document.visibilityState === "hidden") return;
      api(`/api/shared-chats/${threadId}/read`, {
        method: "POST",
        body: JSON.stringify({ message_id: messageId }),
      })
        .then(() => announceSharedChatActivity())
        .catch(() => {});
    },
    [threadId],
  );

  const loadDetail = useCallback(async () => {
    const row = await api<SharedChatDetail>(`/api/shared-chats/${threadId}`, {
      cache: "no-store",
    });
    setDetail(row);
    onTitle?.(row.title);
    return row;
  }, [onTitle, threadId]);

  const loadAgentRuns = useCallback(async () => {
    const rows = await api<SharedChatAgentRun[]>(
      `/api/shared-chats/${threadId}/agent-runs?after=0`,
      { cache: "no-store" },
    );
    setAgentRuns(rows);
    return rows;
  }, [threadId]);

  useEffect(() => {
    const current = ++generation.current;
    latestId.current = 0;
    const target = window.location.hash.match(/^#shared-message-(\d+)$/);
    const messagePath = target
      ? `/api/shared-chats/${threadId}/messages?after=${Math.max(0, Number(target[1]) - 1)}`
      : `/api/shared-chats/${threadId}/messages`;
    Promise.all([
      api<SharedChatDetail>(`/api/shared-chats/${threadId}`, { cache: "no-store" }),
      api<SharedChatMessage[]>(messagePath, {
        cache: "no-store",
      }),
      api<SharedChatAgentRun[]>(`/api/shared-chats/${threadId}/agent-runs?after=0`, {
        cache: "no-store",
      }),
      api<BenchPersona[]>("/api/personas"),
    ])
      .then(([room, rows, runs, bench]) => {
        if (current !== generation.current) return;
        setDetail(room);
        setTitleDraft(room.title);
        onTitle?.(room.title);
        setMessages(rows);
        setAgentRuns(runs);
        setPersonas(bench);
        setHasOlder(rows.length === PAGE_SIZE);
        latestId.current = rows.at(-1)?.id ?? 0;
        markRead(latestId.current);
      })
      .catch((caught) => {
        if (current !== generation.current) return;
        const said = actionError(caught);
        setError(said);
        if (/not found|not available|no shared chat/i.test(said)) onUnavailable?.();
      })
      .finally(() => {
        if (current === generation.current) setLoading(false);
      });
    return () => {
      generation.current += 1;
    };
  }, [markRead, onTitle, onUnavailable, threadId]);

  useEffect(() => {
    let live = true;
    api<Array<{ id: number; name: string; visibility: string }>>("/api/engagements")
      .then((rows) => {
        if (live) {
          setEngagements(
            rows
              .filter((row) => row.visibility === "workspace")
              .map(({ id, name }) => ({ id, name })),
          );
        }
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, []);

  useEffect(() => {
    let cleared = false;
    const clearForIdentity = (event: Event) => {
      if (
        event instanceof StorageEvent &&
        event.key &&
        !["skein-user", "skein-key", "skein-oidc"].includes(event.key)
      ) {
        return;
      }
      if (cleared) return;
      cleared = true;
      generation.current += 1;
      latestId.current = 0;
      retryKey.current = null;
      setDetail(null);
      setMessages([]);
      setAgentRuns([]);
      setDraft("");
      setError("");
      setLoading(false);
      onUnavailable?.();
    };
    window.addEventListener("storage", clearForIdentity);
    window.addEventListener("skein-identity-change", clearForIdentity);
    return () => {
      window.removeEventListener("storage", clearForIdentity);
      window.removeEventListener("skein-identity-change", clearForIdentity);
    };
  }, [onUnavailable]);

  useEffect(() => {
    if (detail?.id && !window.location.hash.startsWith("#shared-message-")) {
      headingRef.current?.focus();
    }
  }, [detail?.id]);

  useEffect(() => {
    if (accessAction) confirmRef.current?.focus();
  }, [accessAction]);

  useEffect(() => {
    let live = true;
    let active = false;
    const refresh = async () => {
      if (!live || active) return;
      active = true;
      try {
        await loadDetail();
      } catch (caught) {
        if (!live) return;
        const said = actionError(caught);
        setError(said);
        if (/not found|not available|no shared chat/i.test(said)) onUnavailable?.();
      } finally {
        active = false;
      }
    };
    if (manageOpen) refresh();
    const timer = window.setInterval(refresh, DETAIL_POLL_MS);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, [loadDetail, manageOpen, onUnavailable]);

  useEffect(() => {
    let live = true;
    let active = false;
    const poll = async () => {
      if (!live || active) return;
      active = true;
      try {
        const [rows, runs] = await Promise.all([
          api<SharedChatMessage[]>(
            `/api/shared-chats/${threadId}/messages?after=${latestId.current}`,
            { cache: "no-store" },
          ),
          api<SharedChatAgentRun[]>(
            `/api/shared-chats/${threadId}/agent-runs?after=0`,
            { cache: "no-store" },
          ),
        ]);
        if (!live) return;
        setAgentRuns(runs);
        if (rows.length > 0) {
          const newest = rows.at(-1);
          latestId.current = Math.max(latestId.current, newest?.id ?? 0);
          setMessages((current) => mergeMessages(current, rows));
          if (newest) setAnnouncement(`New message from ${newest.author || "Skein"}.`);
          markRead(latestId.current);
        }
      } catch (caught) {
        const said = actionError(caught);
        if (/not found|not available|no shared chat/i.test(said)) {
          setMessages([]);
          setDetail(null);
          setError(said);
          onUnavailable?.();
          live = false;
        }
        // Other read faults leave the loaded transcript in place. The next
        // interval retries without claiming that access was revoked.
      } finally {
        active = false;
      }
    };
    const onVisibility = () => {
      if (document.visibilityState === "visible") {
        poll();
        markRead(latestId.current);
      }
    };
    const timer = window.setInterval(poll, POLL_MS);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      live = false;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [markRead, onUnavailable, threadId]);

  const send = async () => {
    const message = draft.trim();
    if (!message || busy || detail?.archived_at) return;
    const calledAgents = invokedAgents(
      message,
      detail?.members
        .filter((member) => member.kind === "agent")
        .map((member) => member.person) ?? [],
    );
    const messageKey =
      retryKey.current?.message === message ? retryKey.current.key : clientKey();
    retryKey.current = { message, key: messageKey };
    setBusy(true);
    setError("");
    try {
      const stored = await api<SharedChatMessage>(
        `/api/shared-chats/${threadId}/messages`,
        {
          method: "POST",
          body: JSON.stringify({
            message,
            client_key: messageKey,
            invoke_agents: calledAgents,
          }),
        },
      );
      latestId.current = Math.max(latestId.current, stored.id);
      setMessages((current) => mergeMessages(current, [stored]));
      retryKey.current = null;
      setDraft("");
      markRead(stored.id);
      if (calledAgents.length) loadAgentRuns().catch(() => {});
      announceSharedChatActivity();
    } catch (caught) {
      setError(actionError(caught));
    } finally {
      setBusy(false);
    }
  };

  const loadOlder = async () => {
    const first = messages[0]?.id;
    if (!first || loadingOlder) return;
    setLoadingOlder(true);
    try {
      const rows = await api<SharedChatMessage[]>(
        `/api/shared-chats/${threadId}/messages?before=${first}`,
        { cache: "no-store" },
      );
      setMessages((current) => mergeMessages(current, rows));
      setHasOlder(rows.length === PAGE_SIZE);
    } catch (caught) {
      setError(actionError(caught));
    } finally {
      setLoadingOlder(false);
    }
  };

  const invite = async () => {
    if (!inviteReview) return;
    setBusy(true);
    try {
      await api(`/api/shared-chats/${threadId}/invitations`, {
        method: "POST",
        body: JSON.stringify({ person: inviteReview, share_history: true }),
      });
      setInviteDraft("");
      setInviteReview("");
      await loadDetail();
      announceSharedChatActivity();
    } catch (caught) {
      setError(actionError(caught));
    } finally {
      setBusy(false);
    }
  };

  const memberWrite = async (path: string, body: object, method = "POST") => {
    setBusy(true);
    try {
      await api(path, { method, body: JSON.stringify(body) });
      await loadDetail();
      announceSharedChatActivity();
      return true;
    } catch (caught) {
      setError(actionError(caught));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const linkEngagement = async (engagementId: number) => {
    setBusy(true);
    try {
      const room = await api<SharedChatDetail>(`/api/shared-chats/${threadId}`, {
        method: "PATCH",
        body: JSON.stringify({ engagement_id: engagementId }),
      });
      setDetail(room);
      announceSharedChatActivity();
    } catch (caught) {
      setError(actionError(caught));
    } finally {
      setBusy(false);
    }
  };

  const rename = async () => {
    if (!titleDraft.trim() || titleDraft.trim() === detail?.title) return;
    setBusy(true);
    try {
      const room = await api<SharedChatDetail>(`/api/shared-chats/${threadId}`, {
        method: "PATCH",
        body: JSON.stringify({ title: titleDraft.trim() }),
      });
      setDetail(room);
      onTitle?.(room.title);
      announceSharedChatActivity();
    } catch (caught) {
      setError(actionError(caught));
    } finally {
      setBusy(false);
    }
  };

  const setArchived = async (archived: boolean) => {
    setBusy(true);
    try {
      const room = await api<SharedChatDetail>(
        `/api/shared-chats/${threadId}/${archived ? "archive" : "restore"}`,
        { method: "POST" },
      );
      setDetail(room);
      announceSharedChatActivity();
      return true;
    } catch (caught) {
      setError(actionError(caught));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const leave = async () => {
    setBusy(true);
    try {
      await api(`/api/shared-chats/${threadId}/leave`, { method: "POST" });
      announceSharedChatActivity();
      onLeave?.();
      return true;
    } catch (caught) {
      setError(actionError(caught));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const cancelAccessAction = () => {
    const trigger = accessAction?.trigger;
    setAccessAction(null);
    setTimeout(() => trigger?.focus(), 0);
  };

  const confirmAccessAction = async () => {
    const action = accessAction;
    if (!action) return;
    setAccessAction(null);
    let succeeded = false;
    if (action.kind === "role") {
      succeeded = await memberWrite(`/api/shared-chats/${threadId}/members/role`, {
        person: action.person,
        role: action.role,
      });
    } else if (action.kind === "remove") {
      succeeded = await memberWrite(
        `/api/shared-chats/${threadId}/members`,
        { person: action.person },
        "DELETE",
      );
    } else if (action.kind === "revoke") {
      succeeded = await memberWrite(
        `/api/shared-chats/${threadId}/invitations`,
        { invitation_id: action.invitationId },
        "DELETE",
      );
    } else if (action.kind === "add-agent") {
      succeeded = await memberWrite(`/api/shared-chats/${threadId}/agents`, {
        agent: action.persona.slug,
        share_history: true,
      });
      if (succeeded) {
        setAgentDraft((current) => (current === action.persona.slug ? "" : current));
      }
    } else if (action.kind === "remove-agent") {
      succeeded = await memberWrite(
        `/api/shared-chats/${threadId}/agents`,
        { agent: action.person },
        "DELETE",
      );
    } else if (action.kind === "archive") {
      succeeded = await setArchived(true);
    } else {
      succeeded = await leave();
    }
    if (action.kind === "leave" && succeeded) return;
    setTimeout(() => {
      if (action.trigger.isConnected && !action.trigger.disabled) {
        action.trigger.focus();
      } else {
        (participantsHeadingRef.current ?? manageButtonRef.current)?.focus();
      }
    }, 0);
  };

  if (loading)
    return <p className="p-8 text-sm text-ink-3">Unrolling the private transcript…</p>;
  if (!detail)
    return (
      <div className="p-8">
        <p role="alert" className="text-sm text-danger">
          {error || "This private shared chat is not available."}
        </p>
      </div>
    );

  const steward = detail.role === "steward";
  const agents = detail.members.filter((member) => member.kind === "agent");
  const agentNames = new Map(personas.map((persona) => [persona.slug, persona.name]));
  const availablePersonas = personas.filter(
    (persona) => !agents.some((member) => member.person === persona.slug),
  );
  const latestRuns = new Map<string, SharedChatAgentRun>();
  for (const run of agentRuns) {
    const current = latestRuns.get(run.agent);
    if (!current || run.trigger_message_id > current.trigger_message_id) {
      latestRuns.set(run.agent, run);
    }
  }
  const visibleRuns = [...latestRuns.values()].filter((run) => run.status !== "completed");
  return (
    <section className="flex min-h-0 flex-1 flex-col" aria-labelledby="shared-chat-title">
      <p role="status" aria-live="polite" className="sr-only">
        {announcement}
      </p>
      <div className="shrink-0 border-b border-line px-3 py-3 sm:px-4">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <h1
              ref={headingRef}
              id="shared-chat-title"
              tabIndex={-1}
              className="font-display text-lg font-semibold text-ink outline-none"
            >
              {detail.title}
            </h1>
            <p className="text-xs text-ink-3">
              Private invite-only chat. Accepted people can read it. Added agents receive the
              chat context only when called.
            </p>
            {detail.engagement_name ? (
              <p className="mt-1 text-xs text-ink-3">
                Engagement: {detail.engagement_name}
              </p>
            ) : null}
          </div>
          <button
            ref={manageButtonRef}
            type="button"
            aria-expanded={manageOpen}
            aria-controls="shared-chat-participants"
            onClick={() => {
              if (manageOpen) setAccessAction(null);
              setManageOpen(!manageOpen);
            }}
            className="min-h-8 rounded-lg border border-line-strong px-2.5 py-1 text-xs text-ink-2 hover:bg-raised"
          >
            Manage participants
          </button>
        </div>
        <p className="mt-2 flex flex-wrap gap-1.5" aria-label="Participants">
          {detail.members.map((member) => (
            <span
              key={member.person}
              className="max-w-full break-all rounded-full bg-raised px-2 py-0.5 text-xs text-ink-2"
            >
              {member.person}
              {member.kind === "agent" ? " · agent" : member.role === "steward" ? " · steward" : ""}
            </span>
          ))}
        </p>
      </div>

      {manageOpen ? (
        <section
          id="shared-chat-participants"
          tabIndex={0}
          aria-labelledby="shared-chat-participants-title"
          className="max-h-[55dvh] shrink-0 overflow-y-auto border-b border-line bg-raised/40 px-3 py-3 sm:px-4"
        >
          <h2
            ref={participantsHeadingRef}
            id="shared-chat-participants-title"
            tabIndex={-1}
            className="text-sm font-semibold text-ink outline-none"
          >
            Participants and access
          </h2>
          {steward ? (
            <>
              <div className="mt-2 flex flex-col gap-2 sm:flex-row">
                <PersonInput
                  aria-label="Invite a teammate"
                  name="shared-chat-invite"
                  value={inviteDraft}
                  onChange={(event) => {
                    setInviteDraft(event.target.value);
                    setInviteReview("");
                  }}
                  className="min-w-0 flex-1 rounded-lg border border-line-strong bg-transparent px-2.5 py-1.5 text-sm outline-none focus:border-thread-solid"
                />
                <button
                  type="button"
                  disabled={!inviteDraft.trim() || busy}
                  onClick={() => setInviteReview(inviteDraft.trim())}
                  className="min-h-9 rounded-lg bg-thread-solid px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40"
                >
                  Review invitation for {inviteDraft.trim() || "teammate"}
                </button>
              </div>
              {inviteReview ? (
                <div className="mt-2 rounded-lg border border-danger/30 bg-danger/5 p-2 text-xs text-danger">
                  <p className="break-all">
                    {inviteReview} can read every message already in this chat.
                  </p>
                  <div className="mt-2 flex gap-2">
                    <button
                      type="button"
                      disabled={busy}
                      onClick={invite}
                      className="min-h-8 rounded-lg bg-danger-solid px-2.5 py-1 font-medium text-white"
                    >
                      Send invitation to {inviteReview}
                    </button>
                    <button
                      type="button"
                      onClick={() => setInviteReview("")}
                      className="min-h-8 rounded-lg px-2.5 py-1 text-ink-2 hover:bg-line"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              ) : null}
              <div className="mt-3 border-t border-line pt-3">
                <h3 className="text-xs font-medium text-ink">Agents</h3>
                <p className="mt-1 text-xs text-ink-3">
                  An added agent stays silent until a participant starts a message with its
                  @mention.
                </p>
                {agents.length < 4 ? (
                  <div className="mt-2 flex flex-col gap-2 sm:flex-row">
                    <label className="min-w-0 flex-1 text-xs text-ink-3">
                      Agent to add
                      <select
                        value={agentDraft}
                        onChange={(event) => setAgentDraft(event.target.value)}
                        className="mt-1 w-full rounded-lg border border-line-strong bg-card px-2.5 py-1.5 text-sm text-ink outline-none focus:border-thread-solid"
                      >
                        <option value="">Select an agent</option>
                        {availablePersonas.map((persona) => (
                          <option key={persona.slug} value={persona.slug}>
                            {persona.name}
                          </option>
                        ))}
                      </select>
                    </label>
                    <button
                      type="button"
                      disabled={!agentDraft || busy}
                      onClick={(event) => {
                        const selected = personas.find((persona) => persona.slug === agentDraft);
                        if (selected) {
                          setAccessAction({
                            kind: "add-agent",
                            persona: selected,
                            trigger: event.currentTarget,
                          });
                        }
                      }}
                      className="min-h-9 self-end rounded-lg bg-thread-solid px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40"
                    >
                      Review agent access
                    </button>
                  </div>
                ) : null}
              </div>
              <ul className="mt-3 space-y-2 text-xs">
                {detail.members.map((member) => (
                  <li key={member.person} className="flex min-w-0 flex-wrap items-center gap-2">
                    <span className="break-all font-medium text-ink">{member.person}</span>
                    <span className="text-ink-3">
                      {member.kind === "agent" ? "agent" : member.role}
                    </span>
                    {member.person !== detail.viewer ? (
                      member.kind === "agent" ? (
                        <button
                          type="button"
                          disabled={busy}
                          onClick={(event) =>
                            setAccessAction({
                              kind: "remove-agent",
                              person: member.person,
                              trigger: event.currentTarget,
                            })
                          }
                          className="min-h-9 break-all rounded bg-danger/10 px-2 py-0.5 text-danger"
                        >
                          Remove agent {member.person}
                        </button>
                      ) : (
                        <>
                          <button
                            type="button"
                            disabled={busy}
                            onClick={(event) =>
                              setAccessAction({
                                kind: "role",
                                person: member.person,
                                role: member.role === "steward" ? "member" : "steward",
                                trigger: event.currentTarget,
                              })
                            }
                            className="min-h-9 break-all rounded bg-card px-2 py-0.5 text-thread"
                          >
                            Make {member.person} a{" "}
                            {member.role === "steward" ? "member" : "steward"}
                          </button>
                          <button
                            type="button"
                            disabled={busy}
                            onClick={(event) =>
                              setAccessAction({
                                kind: "remove",
                                person: member.person,
                                trigger: event.currentTarget,
                              })
                            }
                            className="min-h-9 break-all rounded bg-danger/10 px-2 py-0.5 text-danger"
                          >
                            Remove {member.person}
                          </button>
                        </>
                      )
                    ) : null}
                  </li>
                ))}
              </ul>
              {detail.pending_invitations.length > 0 ? (
                <div className="mt-3">
                  <h3 className="text-xs font-medium text-ink-3">Pending invitations</h3>
                  <ul className="mt-1 space-y-1 text-xs">
                    {detail.pending_invitations.map((invitation) => (
                      <li key={invitation.id} className="flex min-w-0 flex-wrap items-center gap-2">
                        <span className="break-all">{invitation.person}</span>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={(event) =>
                            setAccessAction({
                              kind: "revoke",
                              person: invitation.person,
                              invitationId: invitation.id,
                              trigger: event.currentTarget,
                            })
                          }
                          className="min-h-9 break-all rounded px-2 py-0.5 text-danger hover:bg-danger/10"
                        >
                          Revoke invitation for {invitation.person}
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
              <label className="mt-3 block text-xs text-ink-3">
                Linked engagement
                <select
                  value={detail.engagement_id ?? 0}
                  disabled={busy}
                  onChange={(event) => linkEngagement(Number(event.target.value))}
                  className="mt-1 w-full rounded-lg border border-line-strong bg-card px-2 py-1.5 text-sm text-ink outline-none focus:border-thread-solid"
                >
                  <option value={0}>No linked engagement</option>
                  {engagements.map((engagement) => (
                    <option key={engagement.id} value={engagement.id}>
                      {engagement.name}
                    </option>
                  ))}
                </select>
              </label>
              <div className="mt-3 flex flex-col gap-2 sm:flex-row">
                <label className="min-w-0 flex-1 text-xs text-ink-3">
                  Chat title
                  <input
                    value={titleDraft}
                    onChange={(event) => setTitleDraft(event.target.value)}
                    maxLength={60}
                    className="mt-1 w-full rounded-lg border border-line-strong bg-transparent px-2 py-1.5 text-sm text-ink outline-none focus:border-thread-solid"
                  />
                </label>
                <button
                  type="button"
                  disabled={busy || !titleDraft.trim() || titleDraft.trim() === detail.title}
                  onClick={rename}
                  className="min-h-9 self-end rounded-lg bg-card px-3 py-1.5 text-sm font-medium text-thread disabled:opacity-40"
                >
                  Save chat title
                </button>
              </div>
            </>
          ) : null}
          <div className="mt-3 flex flex-wrap gap-2 border-t border-line pt-3">
            {steward ? (
              <button
                type="button"
                disabled={busy}
                onClick={(event) => {
                  if (detail.archived_at) {
                    setArchived(false);
                  } else {
                    setAccessAction({ kind: "archive", trigger: event.currentTarget });
                  }
                }}
                className="min-h-8 rounded-lg bg-card px-2.5 py-1 text-xs text-ink-2 hover:bg-line"
              >
                {detail.archived_at ? "Restore shared chat" : "Archive shared chat"}
              </button>
            ) : null}
            <button
              type="button"
              disabled={busy}
              onClick={(event) =>
                setAccessAction({ kind: "leave", trigger: event.currentTarget })
              }
              className="min-h-8 rounded-lg bg-danger/10 px-2.5 py-1 text-xs text-danger hover:bg-danger/15"
            >
              Leave shared chat
            </button>
          </div>
          {accessAction ? (
            <div
              role="group"
              aria-labelledby="shared-chat-access-confirm-title"
              className="mt-3 rounded-lg border border-danger/30 bg-danger/5 p-3"
            >
              <h3 id="shared-chat-access-confirm-title" className="text-sm font-semibold text-ink">
                Confirm access change
              </h3>
              <p className="mt-1 text-xs text-danger">
                {accessActionCopy(accessAction).message}
              </p>
              {accessAction.kind === "add-agent" ? (
                <p className="mt-1 text-xs text-danger">
                  The chat history goes to the configured model provider.
                </p>
              ) : null}
              <div className="mt-2 flex flex-wrap gap-2">
                <button
                  ref={confirmRef}
                  type="button"
                  disabled={busy}
                  onClick={confirmAccessAction}
                  className="min-h-8 rounded-lg bg-danger-solid px-2.5 py-1 text-xs font-medium text-white"
                >
                  {accessActionCopy(accessAction).label}
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={cancelAccessAction}
                  className="min-h-8 rounded-lg px-2.5 py-1 text-xs text-ink-2 hover:bg-line"
                >
                  Cancel access change
                </button>
              </div>
            </div>
          ) : null}
        </section>
      ) : null}

      {visibleRuns.length > 0 ? (
        <div aria-live="polite" className="shrink-0 border-b border-line px-3 py-2 sm:px-4">
          {visibleRuns.map((run) => (
            <p
              key={run.turn_id}
              className={
                "text-xs " +
                (run.status === "pending" || run.status === "running"
                  ? "text-ink-3"
                  : "text-danger")
              }
            >
              {runMessage(run)}
            </p>
          ))}
        </div>
      ) : null}

      <div
        tabIndex={0}
        aria-label="Chat messages"
        className="min-h-0 flex-1 overflow-y-auto px-3 py-3 sm:px-4"
      >
        {hasOlder ? (
          <button
            type="button"
            disabled={loadingOlder}
            onClick={loadOlder}
            className="mb-3 w-full rounded-lg border border-line px-3 py-1.5 text-xs text-ink-2 hover:bg-raised"
          >
            {loadingOlder ? "Loading older messages…" : "Load older messages"}
          </button>
        ) : null}
        {messages.length === 0 ? (
          <p className="rounded-xl border border-dashed border-line-strong p-6 text-center text-sm text-ink-3">
            No messages yet. Start the private conversation below.
          </p>
        ) : (
          <ol className="space-y-3">
            {messages.map((message) => {
              const mine = message.author === detail.viewer;
              return (
                <li
                  key={message.id}
                  id={`shared-message-${message.id}`}
                  tabIndex={-1}
                  className={`${mine ? "flex justify-end" : "flex justify-start"} ${HASH_TARGET}`}
                >
                  <article
                    className={
                      "max-w-[85%] rounded-xl px-3 py-2 text-sm " +
                      (mine ? "bg-thread-solid text-white" : "bg-raised text-ink")
                    }
                  >
                    <p
                      className={
                        "break-all text-xs " + (mine ? "text-white/80" : "text-ink-3")
                      }
                    >
                      {message.author || "Skein"}
                      {message.author_kind === "agent" ? " · agent" : ""} ·{" "}
                      {timeAgo(message.created_at)}
                    </p>
                    <p className="whitespace-pre-wrap break-words">{message.content}</p>
                  </article>
                </li>
              );
            })}
          </ol>
        )}
      </div>

      <div className="shrink-0 border-t border-line bg-page px-3 py-3 sm:px-4">
        {error ? (
          <p role="alert" className="mb-2 text-xs text-danger">
            {error}
          </p>
        ) : null}
        {detail.archived_at ? (
          <p className="text-sm text-ink-3">
            This shared chat is archived. A steward can restore it from participant management.
          </p>
        ) : (
          <div>
            {agents.length > 0 ? (
              <div className="mb-2 flex flex-wrap items-center gap-1.5 text-xs text-ink-3">
                <span>Call an agent:</span>
                {agents.map((member) => (
                  <button
                    key={member.person}
                    type="button"
                    aria-label={`Call ${agentNames.get(member.person) || member.person} (@${member.person})`}
                    onClick={() => {
                      retryKey.current = null;
                      setDraft((current) =>
                        addAgentMention(
                          current,
                          member.person,
                          agents.map((agent) => agent.person),
                        ),
                      );
                      setTimeout(() => composerRef.current?.focus(), 0);
                    }}
                    className="min-h-9 rounded-lg bg-raised px-2.5 py-1.5 font-mono text-thread hover:bg-line"
                  >
                    @{member.person}
                  </button>
                ))}
              </div>
            ) : null}
            <div className="flex items-end gap-2">
              <label className="sr-only" htmlFor="shared-chat-composer">
                Message {detail.title}
              </label>
              <textarea
                ref={composerRef}
                id="shared-chat-composer"
                name="shared-chat-message"
                value={draft}
                onChange={(event) => {
                  const value = event.target.value;
                  if (retryKey.current && value.trim() !== retryKey.current.message) {
                    retryKey.current = null;
                  }
                  setDraft(value);
                }}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    send();
                  }
                }}
                rows={2}
                maxLength={20_000}
                placeholder="Write to the accepted participants…"
                className="max-h-36 min-w-0 flex-1 resize-y rounded-xl border border-line-strong bg-card px-3 py-2 text-sm outline-none focus:border-thread-solid"
              />
              <button
                type="button"
                disabled={busy || !draft.trim()}
                onClick={send}
                className="min-h-10 rounded-lg bg-thread-solid px-4 py-2 text-sm font-medium text-white disabled:opacity-40"
              >
                Send message
              </button>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
